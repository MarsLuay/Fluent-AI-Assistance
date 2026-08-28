"""Export compiled artifacts into script-centered user handoff folders."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from . import xml_compat as ET
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping

from fluentcoder.expressions import (
    expression_inventory_from_xscr_text,
)

from .checksums import (
    audit_archive_checksums,
    entry_checksum_state,
    recompute_checksum_bytes,
)
from .fluentcontrol_inventory import (
    build_scripts_inventory,
    collision_preflight,
    find_local_script_guid,
    report_missing_system_dependencies,
    rewrite_script_reference_guids,
)
from .bundle_media import (
    SOURCE_MEDIA_ORIGINALS_DIR,
    assign_step_label_media_to_final_prompts,
    organize_bundle_touchtools_media,
)
from .compiled_xscr_finalizer import finalize_compiled_xscr
from .bundle_lifecycle import (
    created_from_record,
    lifecycle_metadata,
    source_export_kind,
    verification_state_from_readiness,
)
from .config import FAILED_PACKAGES_DIR, PACKAGE_STAGING_DIR, READY_TO_IMPORT_DIR
from .delivery_bundle import (
    DELIVERY_MANIFEST_SCHEMA_VERSION,
    READY_BUNDLE_SCHEMA_VERSION,
    delivery_bundle_failure_message,
    validate_v2_delivery_bundle,
)
from .expression_provenance import (
    EXPRESSION_PROVENANCE_FILENAME,
    source_preserved_expression_context_from_bundle,
)
from .protocol_ir import load_protocol_ir, protocol_ir_from_python, render_recreate_markdown, write_protocol_ir
from .protocol_ir import (
    apply_touchtools_media_path_map_to_xscr,
    build_media_path_map,
    render_media_path_map_markdown,
    resolve_touchtools_images_dir,
    resolve_touchtools_media_subfolder,
)
from .readiness import (
    build_canonical_readiness,
    embed_readiness,
    readiness_status_from_readiness,
)
from .request_spec_resolver import normalize_protocol_stem, split_version_suffix
from .runner import PipelineError, ensure_parent, write_json
from .subroutine_dependencies import (
    find_subroutine_record as _resolve_subroutine_record,
    norm_subroutine_key,
    subroutine_dependency_records_from_artifacts,
)
from .validation import (
    render_validation_markdown,
    validate_ready_to_import,
    validation_failure_message,
)
from .variable_namespaces import localize_variable_declaration_namespaces
from .worktable_diff import (
    diff_worktable_requirements,
    render_worktable_changes_markdown,
    render_worktable_patch_json,
)
from .zeia_filesystem import (
    audit_archive_filesystem,
    collect_file_reference_paths,
    copy_referenced_filesystem_from_archives,
    embed_filesystem_in_archive,
    ensure_script_file_references,
    extract_archive_filesystem_payloads,
    parse_fs_mapping_directories,
    plan_fs_embed,
    strip_orphan_touchtools_media_file_references,
)


BUNDLE_SCHEMA_VERSION = READY_BUNDLE_SCHEMA_VERSION
STRICT_REPORT_FILENAMES = {
    "project_report": "project_report.md",
    "simulation_report": "simulation_report.md",
    "repair_plan": "repair_plan.md",
    "repair_history": "repair_history.json",
    "compile_report": "compile_report.md",
}
HARDWARE_MANIFEST_SCHEMA_VERSION = "tecan.hardware_manifest.v1"
METHOD_TOUCHTOOLS_SCHEMA_VERSION = "tecan.method_touchtools_readiness.v1"
ASSET_SUFFIXES = {".bmp", ".gif", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
FLUENT_IMPORT_UNSUPPORTED_DATASTORE_TYPES = frozenset(
    {
        "LiquidClass",
        "WorktableComponent",
        "WorktableWorkspace",
    }
)
FLUENT_IMPORT_UNSUPPORTED_DATASTORE_KEYS = frozenset({"5", "9", "12"})
_VERSIONED_FOLDER_RE = re.compile(r"^(?P<base>.+)_v(?P<version>\d+)$", re.IGNORECASE)
_ARCHIVE_WRITER_SHARED_DLL = Path(r"C:\Program Files (x86)\Common Files\Tecan\Core\Recent\Tecan.VisionX.ExportImportArchive.Shared.dll")
_ARCHIVE_WRITER_IMPL_DLL = Path(r"C:\Program Files (x86)\Tecan\FluentControl\Tecan.VisionX.ExportImportArchive.dll")
class _ArchiveWriterUnavailable(RuntimeError):
    """Raised when the FluentControl archive writer cannot run locally."""


@dataclass(frozen=True)
class ExportedArtifact:
    source: Path
    destination: Path
    kind: str


@dataclass(frozen=True)
class ReadyBundleStage:
    staging_root: Path
    script_dir: Path
    bundle_name: str
    final_bundle_dir: Path
    failed_bundle_dir: Path
    validation_report: dict[str, Any]
    verification_state: str
    exports: list[ExportedArtifact]
    metadata_path: Path
    protocol_name: str | None = None


@dataclass(frozen=True)
class ReadyBundlePublishPlan:
    ready_root: Path
    base_name: str
    bundle_name: str
    bundle_dir: Path
    staging_dir: Path
    backup_dir: Path
    archive_path: Path


class _ReadyBundleTransactionError(PipelineError):
    """Raised when a staged bundle fails at a specific transaction boundary."""

    def __init__(self, failure_point: str, message: str, *, original: BaseException | None = None):
        super().__init__(message)
        self.failure_point = failure_point
        self.original = original


def _raise_bundle_transaction_error(
    failure_point: str,
    exc: BaseException,
    *,
    detail: str,
) -> None:
    if isinstance(exc, _ReadyBundleTransactionError):
        raise exc
    raise _ReadyBundleTransactionError(failure_point, f"{detail}: {exc}", original=exc) from exc


def export_ready_to_import(
    compiled_xscr: Path,
    *,
    bundle_name: str | None = None,
    context_name: str | None = None,
    draft_path: Path | None = None,
    source_xscr: Path | None = None,
    protocol_ir: Path | None = None,
    expression_provenance: Path | None = None,
    worklist: Path | None = None,
    source_projects: list[Path] | None = None,
    filesystem_source_projects: list[Path] | None = None,
    source_scripts: list[Path] | None = None,
    source_manifest: dict[str, Any] | None = None,
    report_files: dict[str, Path] | None = None,
    worktable_changes: Path | None = None,
    worktable_patch: Path | None = None,
    recreate_guide: Path | None = None,
    request_spec: Path | None = None,
    validation_diff: Path | None = None,
    validation_diff_json: Path | None = None,
    validation_context: dict[str, Any] | None = None,
    target_script_folder: str | None = None,
    reports: list[Path] | None = None,
    media_dir: Path | None = None,
    publish: bool = True,
    export_summary: dict[str, Any] | None = None,
) -> list[ExportedArtifact] | ReadyBundleStage:
    """Copy artifacts into a strict `ready-to-import/<script>/` bundle."""
    finalization_source = (
        protocol_ir
        if protocol_ir is not None and protocol_ir.exists()
        else draft_path
        if draft_path is not None and draft_path.exists()
        else source_xscr
        if source_xscr is not None and source_xscr.exists()
        else compiled_xscr
    )
    finalization_report = finalize_compiled_xscr(
        compiled_xscr,
        finalization_source,
        source_manifest,
        [
            *(source_scripts or []),
            *([source_xscr] if source_xscr is not None else []),
        ],
        {"source_ir_origin": "export_ready_to_import"},
    )
    if not finalization_report.ok:
        summary = "; ".join(finalization_report.errors[:3]) or "mandatory compiled XSCR finalization failed"
        raise PipelineError(f"compiled XSCR finalization failed: {summary}")

    script_name = _safe_label(bundle_name or compiled_xscr.stem)
    ready_root = READY_TO_IMPORT_DIR
    final_name = next_ready_bundle_name(ready_root, script_name)
    run_id = _package_run_id()
    staging_bundle = PACKAGE_STAGING_DIR / run_id / script_name
    failed_bundle = FAILED_PACKAGES_DIR / run_id / script_name
    published_bundle = ready_root / final_name
    bundle_root = staging_bundle
    staging_run_root = staging_bundle.parent

    validation_report = validate_ready_to_import(
        compiled_xscr=compiled_xscr,
        draft_path=draft_path,
        protocol_ir=protocol_ir,
        expression_provenance=expression_provenance,
        worklist=worklist,
        source_projects=source_projects or [],
        source_scripts=source_scripts or [],
        source_xscr=source_xscr,
        provenance_source_artifacts=[
            *(filesystem_source_projects or source_projects or []),
            *(source_scripts or []),
            *([source_xscr] if source_xscr is not None else []),
        ],
        source_manifest=source_manifest,
        recreate_guide=recreate_guide,
        validation_context=validation_context or {},
    )
    if not validation_report["ready"]:
        raise PipelineError(validation_failure_message(validation_report))

    script_dir = bundle_root
    direct_imports_dir = script_dir / "direct-imports"
    direct_scripts_dir = direct_imports_dir / "scripts"
    full_script_dir = direct_scripts_dir / "full-script"
    direct_subroutines_dir = direct_scripts_dir / "subroutines"
    direct_worklists_dir = direct_imports_dir / "worklists"
    direct_connectors_dir = direct_imports_dir / "hardware-connectors"
    direct_projects_dir = direct_imports_dir / "projects" / "full-project"
    source_dir = script_dir / "source"
    reports_dir = source_dir / "reports"
    original_sources_dir = source_dir / "original-sources"
    source_subroutines_dir = source_dir / "subroutines"
    hardware_dir = source_dir / "hardware"
    metadata_dest = source_dir / "metadata.json"

    exports: list[ExportedArtifact] = []
    copied_files: list[dict[str, str]] = []
    try:
        _reset_strict_bundle(script_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)
        original_sources_dir.mkdir(parents=True, exist_ok=True)
        if media_dir is not None and media_dir.is_dir():
            staged_media = script_dir / "media"
            staged_media.mkdir(parents=True, exist_ok=True)
            for path in media_dir.iterdir():
                if path.is_file():
                    shutil.copy2(path, staged_media / path.name)

        protocol_ir_dest = source_dir / "protocol.ir.json"
        if protocol_ir is not None and protocol_ir.exists():
            _copy_record(protocol_ir, protocol_ir_dest, "protocol-ir", exports, copied_files, bundle_root=bundle_root)
        elif draft_path is not None and draft_path.exists():
            _write_protocol_ir_from_draft(draft_path, protocol_ir_dest)
            exports.append(ExportedArtifact(protocol_ir_dest, protocol_ir_dest, "protocol-ir"))
            copied_files.append(_file_record("protocol-ir", protocol_ir_dest, protocol_ir_dest, bundle_root=bundle_root))
        else:
            _write_unavailable_json(
                protocol_ir_dest,
                "canonical protocol IR was not provided and could not be derived",
            )
            exports.append(ExportedArtifact(protocol_ir_dest, protocol_ir_dest, "protocol-ir"))
            copied_files.append(_file_record("protocol-ir", protocol_ir_dest, protocol_ir_dest, bundle_root=bundle_root))

        draft_dest = source_dir / "protocol_draft.py"
        if draft_path is not None and draft_path.exists():
            _copy_record(draft_path, draft_dest, "protocol-draft", exports, copied_files, bundle_root=bundle_root)
        else:
            draft_dest.write_text(
                "# No protocol draft was exported with this bundle.\n",
                encoding="utf-8",
            )
            exports.append(ExportedArtifact(draft_dest, draft_dest, "protocol-draft"))
            copied_files.append(_file_record("protocol-draft", draft_dest, draft_dest, bundle_root=bundle_root))

        script_dest = full_script_dir / "generated_script.xscr"
        _copy(compiled_xscr, script_dest)
        exports.append(ExportedArtifact(compiled_xscr, script_dest, "compiled-script"))
        copied_files.append(_file_record("compiled-script", compiled_xscr, script_dest, bundle_root=bundle_root))
        media_path_map, generated_media_dir = _prepare_generated_touchtools_media(
            protocol_ir_dest,
            script_dest,
            script_dir=script_dir,
            source_dir=source_dir,
            reports_dir=reports_dir,
        )
        packaged_xscr = script_dest

        worklist_present = bool(worklist and worklist.exists())
        if worklist_present:
            worklist_dest = direct_worklists_dir / "generated_worklist.gwl"
            _copy_record(worklist, worklist_dest, "generated-worklist", exports, copied_files, bundle_root=bundle_root)

        source_paths = _dedupe_paths(
            [
                *(source_projects or []),
                *(filesystem_source_projects or []),
                *(source_scripts or []),
                *([source_xscr] if source_xscr is not None else []),
            ]
        )
        source_counters: dict[str, int] = {}
        for source in source_paths:
            if not source.exists():
                continue
            kind = _original_source_kind(source)
            source_counters[kind] = source_counters.get(kind, 0) + 1
            destination = original_sources_dir / _original_source_name(source, kind, source_counters[kind])
            try:
                _copy_record(source, destination, kind, exports, copied_files, bundle_root=bundle_root)
            except Exception as exc:
                _raise_bundle_transaction_error(
                    "archive_copying",
                    exc,
                    detail=f"Could not copy source artifact {source} into the staged bundle",
                )

        expression_provenance_dest = reports_dir / EXPRESSION_PROVENANCE_FILENAME
        if expression_provenance is not None and expression_provenance.exists():
            _copy_record(
                expression_provenance,
                expression_provenance_dest,
                "expression-provenance",
                exports,
                copied_files,
                bundle_root=bundle_root,
            )

        subroutine_artifacts = _dedupe_subroutine_artifacts(
            _resolved_subroutine_artifacts(
                source_manifest,
                parent_scripts=[
                    *(source_scripts or []),
                    *([source_xscr] if source_xscr is not None else []),
                ],
            )
        )
        subroutine_records: list[dict[str, str]] = []
        if subroutine_artifacts:
            for index, item in enumerate(subroutine_artifacts, start=1):
                source = item["path"]
                destination = direct_subroutines_dir / _subroutine_artifact_name(item, index)
                _copy_record(source, destination, "subroutine-script", exports, copied_files, bundle_root=bundle_root)
                subroutine_records.append(
                    {
                        "ref": item.get("ref", ""),
                        "object_name": item.get("object_name", ""),
                        "folder": item.get("folder", ""),
                        "guid": item.get("guid", ""),
                        "entry": item.get("entry", ""),
                        "version": item.get("version", ""),
                        "source_context": item.get("source_context", ""),
                        "source_path": str(source),
                        "relative_path": _bundle_relative_path(destination, bundle_root=bundle_root),
                        "ambiguous": bool(item.get("ambiguous")),
                        "alternatives": item.get("alternatives") or [],
                    }
                )
            manifest_dest = source_subroutines_dir / "SUBROUTINES.md"
            ensure_parent(manifest_dest)
            try:
                manifest_dest.write_text(_render_subroutine_manifest(subroutine_records), encoding="utf-8")
            except Exception as exc:
                _raise_bundle_transaction_error(
                    "manifest_construction",
                    exc,
                    detail=f"Could not write staged subroutine manifest {manifest_dest}",
                )
            exports.append(ExportedArtifact(manifest_dest, manifest_dest, "subroutine-manifest"))
            copied_files.append(_file_record("subroutine-manifest", manifest_dest, manifest_dest, bundle_root=bundle_root))

        hardware_report = _resolved_hardware_artifacts(
            source_manifest,
            script_paths=[
                *(source_scripts or []),
                *([source_xscr] if source_xscr is not None else []),
                *[Path(item["path"]) for item in subroutine_artifacts if item.get("path")],
            ],
        )
        if _has_hardware_evidence(hardware_report):
            hardware_report = _write_hardware_artifacts(
                hardware_report,
                script_dir=source_dir,
                hardware_dir=hardware_dir,
                direct_connectors_dir=direct_connectors_dir,
                bundle_root=bundle_root,
                exports=exports,
                copied_files=copied_files,
            )

        labware_catalog_dest = _write_labware_catalog_artifact(
            source_manifest,
            source_dir=source_dir,
            bundle_root=bundle_root,
            exports=exports,
            copied_files=copied_files,
        )
        connector_coverage_dest = _write_connector_coverage_artifact(
            source_manifest,
            source_dir=source_dir,
            bundle_root=bundle_root,
            exports=exports,
            copied_files=copied_files,
        )
        connector_graph_dest = _write_connector_graph_artifact(
            source_manifest,
            source_dir=source_dir,
            bundle_root=bundle_root,
            exports=exports,
            copied_files=copied_files,
        )
        liquid_classes_dest = _write_liquid_classes_artifact(
            source_manifest,
            source_dir=source_dir,
            bundle_root=bundle_root,
            exports=exports,
            copied_files=copied_files,
        )
        driver_macros_dest = _write_driver_macros_artifact(
            source_manifest,
            source_dir=source_dir,
            bundle_root=bundle_root,
            exports=exports,
            copied_files=copied_files,
        )
        script_folder_bindings_dest = _write_script_folder_bindings_artifact(
            source_manifest,
            source_dir=source_dir,
            bundle_root=bundle_root,
            exports=exports,
            copied_files=copied_files,
        )

        method_touchtools_report = _method_touchtools_readiness_report(
            packaged_xscr,
            source_manifest=source_manifest,
            script_paths=[
                *(source_scripts or []),
                *([source_xscr] if source_xscr is not None else []),
                *[Path(item["path"]) for item in subroutine_artifacts if item.get("path")],
            ],
            request_spec=request_spec,
        )
        _write_method_touchtools_artifacts(
            method_touchtools_report,
            script_dir=source_dir,
            reports_dir=reports_dir,
            bundle_root=bundle_root,
            exports=exports,
            copied_files=copied_files,
        )

        try:
            project_import_records = _write_project_import_archives(
                source_projects or [],
                filesystem_source_archives=filesystem_source_projects or source_projects or [],
                compiled_xscr=packaged_xscr,
                destination_dir=direct_projects_dir,
                bundle_root=bundle_root,
                source_manifest=source_manifest,
                source_xscr=source_xscr,
                source_scripts=source_scripts or [],
                subroutine_artifacts=subroutine_artifacts,
                media_dir=generated_media_dir,
                media_path_map=media_path_map,
                target_script_folder=target_script_folder,
                exports=exports,
                copied_files=copied_files,
            )
        except zipfile.BadZipFile as exc:
            _raise_bundle_transaction_error(
                "zip_extraction",
                exc,
                detail="Could not extract the source ZEIA archive while building the staged generated project",
            )
        except Exception as exc:
            _raise_bundle_transaction_error(
                "generated_zeia_creation",
                exc,
                detail="Could not create the staged generated project ZEIA",
            )
        if project_import_records:
            _write_project_import_report_artifacts(
                project_import_records,
                reports_dir=reports_dir,
                bundle_root=bundle_root,
                exports=exports,
                copied_files=copied_files,
            )
            # Re-validate now that the generated ZEIA exists on disk: the pre-flight run
            # above can only predict, but Gate 23/24 can audit the actual archive.
            post_package_context = {**(validation_context or {}), **_merge_project_audits(project_import_records)}
            try:
                validation_report = validate_ready_to_import(
                    compiled_xscr=packaged_xscr,
                    draft_path=draft_path,
                    protocol_ir=protocol_ir,
                    expression_provenance=expression_provenance,
                    worklist=worklist,
                    source_projects=source_projects or [],
                    source_scripts=source_scripts or [],
                    source_xscr=source_xscr,
                    provenance_source_artifacts=[
                        *(filesystem_source_projects or source_projects or []),
                        *(source_scripts or []),
                        *([source_xscr] if source_xscr is not None else []),
                    ],
                    source_manifest=source_manifest,
                    recreate_guide=recreate_guide,
                    validation_context=post_package_context,
                )
            except Exception as exc:
                _raise_bundle_transaction_error(
                    "post_package_validation",
                    exc,
                    detail="Could not validate the staged generated project ZEIA",
                )

        manual_recreation = _manual_recreation_details(
            packaged_xscr,
            draft_path=draft_path,
            source_xscr=source_xscr,
        )

        readiness = build_canonical_readiness(
            validation_report=validation_report,
            package_outputs=[artifact.destination for artifact in exports],
        )
        readiness_status = readiness_status_from_readiness(
            readiness,
            workflow_status="ready_to_import" if validation_report.get("ready") else "validated_not_ready",
        )
        embed_readiness(
            validation_report,
            readiness=readiness,
            readiness_status=readiness_status,
        )

        report_map = _strict_report_map(report_files or {}, reports or [])
        for key, filename in STRICT_REPORT_FILENAMES.items():
            destination = reports_dir / filename
            report_source = report_map.get(key)
            if report_source is not None and report_source.exists():
                _copy_record(report_source, destination, key, exports, copied_files, bundle_root=bundle_root)
            else:
                destination.write_text(_placeholder_report(key), encoding="utf-8")
                exports.append(ExportedArtifact(destination, destination, key))
                copied_files.append(_file_record(key, destination, destination, bundle_root=bundle_root))

        for extra_index, source in enumerate(report_map.get("supporting_reports", []), start=1):
            if not source.exists():
                continue
            destination = reports_dir / f"supporting_report_{extra_index}{source.suffix or '.md'}"
            _copy_record(source, destination, "supporting-report", exports, copied_files, bundle_root=bundle_root)

        validation_dest = reports_dir / "validation_report.md"
        validation_json_dest = reports_dir / "validation_report.json"
        validation_dest.write_text(render_validation_markdown(validation_report), encoding="utf-8")
        write_json(validation_json_dest, validation_report)
        exports.append(ExportedArtifact(validation_dest, validation_dest, "validation-report"))
        copied_files.append(_file_record("validation-report", validation_dest, validation_dest, bundle_root=bundle_root))
        exports.append(ExportedArtifact(validation_json_dest, validation_json_dest, "validation-report-json"))
        copied_files.append(
            _file_record("validation-report-json", validation_json_dest, validation_json_dest, bundle_root=bundle_root)
        )

        worktable_changes_dest = source_dir / "worktable_changes.md"
        if worktable_changes is not None and worktable_changes.exists():
            _copy_record(
                worktable_changes,
                worktable_changes_dest,
                "worktable-changes",
                exports,
                copied_files,
                bundle_root=bundle_root,
            )
        elif _write_worktable_changes_from_ir(
            protocol_ir_dest,
            worktable_changes_dest,
            source_manifest=source_manifest,
            source_xscr=source_xscr,
            source_scripts=source_scripts or [],
        ):
            exports.append(ExportedArtifact(worktable_changes_dest, worktable_changes_dest, "worktable-changes"))
            copied_files.append(
                _file_record("worktable-changes", worktable_changes_dest, worktable_changes_dest, bundle_root=bundle_root)
            )
        else:
            worktable_changes_dest.write_text(
                _render_worktable_changes_unavailable(protocol_ir_dest),
                encoding="utf-8",
            )
            exports.append(ExportedArtifact(worktable_changes_dest, worktable_changes_dest, "worktable-changes"))
            copied_files.append(
                _file_record("worktable-changes", worktable_changes_dest, worktable_changes_dest, bundle_root=bundle_root)
            )

        worktable_patch_dest = source_dir / "worktable.patch.json"
        if worktable_patch is not None and worktable_patch.exists():
            _copy_record(
                worktable_patch,
                worktable_patch_dest,
                "worktable-patch",
                exports,
                copied_files,
                bundle_root=bundle_root,
            )
        elif _write_worktable_patch_from_ir(
            protocol_ir_dest,
            worktable_patch_dest,
            source_manifest=source_manifest,
            source_xscr=source_xscr,
            source_scripts=source_scripts or [],
        ):
            exports.append(ExportedArtifact(worktable_patch_dest, worktable_patch_dest, "worktable-patch"))
            copied_files.append(
                _file_record("worktable-patch", worktable_patch_dest, worktable_patch_dest, bundle_root=bundle_root)
            )
        else:
            worktable_patch_dest.write_text(
                _render_worktable_patch_unavailable(protocol_ir_dest),
                encoding="utf-8",
            )
            exports.append(ExportedArtifact(worktable_patch_dest, worktable_patch_dest, "worktable-patch"))
            copied_files.append(
                _file_record("worktable-patch", worktable_patch_dest, worktable_patch_dest, bundle_root=bundle_root)
            )

        request_spec_dest = source_dir / "request.spec.yaml"
        if request_spec is not None and request_spec.exists():
            _copy_record(request_spec, request_spec_dest, "request-spec", exports, copied_files, bundle_root=bundle_root)
        else:
            request_spec_dest.write_text(
                _minimal_harness_request_spec_yaml(script_name),
                encoding="utf-8",
            )
            exports.append(ExportedArtifact(request_spec_dest, request_spec_dest, "request-spec"))
            copied_files.append(
                _file_record("request-spec", request_spec_dest, request_spec_dest, bundle_root=bundle_root)
            )

        validation_diff_dest = source_dir / "validation_diff.md"
        if validation_diff is not None and validation_diff.exists():
            _copy_record(validation_diff, validation_diff_dest, "validation-diff", exports, copied_files, bundle_root=bundle_root)

        validation_diff_json_dest = source_dir / "validation_diff.json"
        if validation_diff_json is not None and validation_diff_json.exists():
            _copy_record(
                validation_diff_json,
                validation_diff_json_dest,
                "validation-diff-json",
                exports,
                copied_files,
                bundle_root=bundle_root,
            )

        hardware_evidence_present = _has_hardware_evidence(hardware_report)
        hardware_connectors_present = _has_packaged_hardware_connectors(hardware_report)
        full_zeia_export = (validation_context or {}).get("full_zeia_export") or {}
        approved_partial = bool((validation_context or {}).get("partial_zeia_export_approved"))
        workflow_status = "ready_to_import" if validation_report.get("ready") else "validated_not_ready"
        lifecycle = lifecycle_metadata(
            bundle_role="ready" if validation_report.get("ready") else "debug",
            source_export_kind=source_export_kind(full_zeia_export, approved_partial=approved_partial),
            verification_state=verification_state_from_readiness(
                ready_to_import=bool(validation_report.get("ready")),
                readiness=readiness,
                workflow_status=workflow_status,
            ),
            created_from=created_from_record(
                context_name=context_name,
                context_kind=(validation_context or {}).get("context_kind"),
                source_contexts=(validation_context or {}).get("source_contexts") or [],
                source_projects=source_projects or [],
            ),
        )
        metadata = {
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "script_name": script_name,
            "context_name": context_name,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bundle_role": lifecycle["bundle_role"],
            "ready_to_import": bool(validation_report.get("ready")),
            "source_export_kind": lifecycle["source_export_kind"],
            "verification_state": lifecycle["verification_state"],
            "readiness_status": readiness_status,
            "readiness": readiness,
            "supersedes": lifecycle["supersedes"],
            "superseded_by": lifecycle["superseded_by"],
            "lifecycle": lifecycle,
            "cleanup_guidance": {
                "safe_default": "Use `python -m fluent_pipeline.cli bundle-lifecycle` for a dry-run inventory before moving anything.",
                "archive_command": "Use `python -m fluent_pipeline.cli bundle-lifecycle --archive` to move recommended old/probe/debug outputs into an archive folder.",
                "delete_policy": "Do not delete generated bundles manually until a dry-run report has been reviewed.",
            },
            "generated_worklist_present": worklist_present,
            "direct_xscr_import": {
                "path": "direct-imports/scripts/full-script/generated_script.xscr",
                "kind": "direct-xscr-import",
                "description": (
                    "Import this XSCR directly when you only need the generated FluentControl script file. "
                    "It is not a project archive and does not carry source ZEIA dependencies."
                ),
            },
            "zeia_import": (
                {
                    "path": "direct-imports/projects/full-project/generated_project.zeia",
                    "kind": "generated-zeia-import",
                    "description": (
                        "Import this ZEIA when you want FluentControl's archive importer to add the "
                        "generated script object while resolving existing context dependencies from the target system."
                    ),
                    "packaging_method": project_import_records[0].get("packaging_method") if project_import_records else None,
                }
                if project_import_records
                else None
            ),
            "artifact_roles": {
                "direct_xscr": {
                    "path": "direct-imports/scripts/full-script/generated_script.xscr",
                    "role": "standalone script import",
                    "not": "source ZEIA, generated project ZEIA, probe archive, or Script Editor load certificate",
                },
                "generated_project_zeia": (
                    {
                        "path": "direct-imports/projects/full-project/generated_project.zeia",
                        "role": "generated project import archive built from the source ZEIA base",
                        "not": "the original/source ZEIA, a debug/probe ZEIA, or proof that Script Editor can open the method",
                    }
                    if project_import_records
                    else None
                ),
                "source_zeia": {
                    "path": "source/original-sources/",
                    "role": "audit copy of user-provided source archive(s)",
                    "not": "an output to import as the generated method",
                },
                "probe_outputs": {
                    "role": "debug-only artifacts when present outside this strict bundle",
                    "not": "final outputs unless listed in this metadata files inventory",
                },
            },
            "readiness_boundaries": {
                "import_clean": "Checksums and generated ZEIA archive structure are import-safe when project_import_report.md says import-clean/import-ready.",
                "script_editor_load_clean": "Not certified by this metadata. Requires the optional Gate 27 FluentControl import/load diagnostic or a manual Script Editor open/load check of the generated artifact.",
                "simulation_clean": "Covered by validation_report.md Gate 7 for offline simulation; any live runtime evidence comes from the optional FluentControl import/load diagnostic.",
                "hardware_run_ready": "Never certified by the ready-to-import bundle; requires operator review on the target instrument.",
            },
            "layout": {
                "recreate_script": "RECREATE_SCRIPT.md",
                "direct_imports": "direct-imports/",
                "direct_import_scripts": "direct-imports/scripts/",
                "full_script_folder": "direct-imports/scripts/full-script/",
                "generated_script": "direct-imports/scripts/full-script/generated_script.xscr",
                "subroutines": "direct-imports/scripts/subroutines/" if subroutine_records else None,
                "generated_worklist": "direct-imports/worklists/generated_worklist.gwl" if worklist_present else None,
                "hardware_connectors": "direct-imports/hardware-connectors/" if hardware_connectors_present else None,
                "project_imports": "direct-imports/projects/" if project_import_records else None,
                "full_project_folder": "direct-imports/projects/full-project/" if project_import_records else None,
                "generated_project": "direct-imports/projects/full-project/generated_project.zeia" if project_import_records else None,
                "source": "source/",
                "protocol_ir": "source/protocol.ir.json",
                "expression_provenance": (
                    f"source/reports/{EXPRESSION_PROVENANCE_FILENAME}"
                    if expression_provenance_dest.exists()
                    else None
                ),
                "protocol_draft": "source/protocol_draft.py",
                "original_sources": "source/original-sources/",
                "subroutine_manifest": "source/subroutines/SUBROUTINES.md" if subroutine_records else None,
                "hardware": "source/hardware/" if hardware_evidence_present else None,
                "hardware_manifest": "source/hardware/hardware_manifest.json" if hardware_evidence_present else None,
                "labware_catalog": "source/labware_catalog.json" if labware_catalog_dest else None,
                "connector_coverage": "source/connector_coverage.json" if connector_coverage_dest else None,
                "connector_graph": "source/connector_graph.json" if connector_graph_dest else None,
                "liquid_classes": "source/liquid_classes.json" if liquid_classes_dest else None,
                "driver_macros": "source/driver_macros.json" if driver_macros_dest else None,
                "script_folder_bindings": (
                    "source/script_folder_bindings.json" if script_folder_bindings_dest else None
                ),
                "hardware_pins_checklist": "source/HARDWARE_PINS.md" if hardware_evidence_present else None,
                "method_touchtools_readiness": "source/METHOD_TOUCHTOOLS_READINESS.md",
                "method_touchtools_readiness_json": "source/reports/method_touchtools_readiness.json",
                "project_import_report": "source/reports/project_import_report.md" if project_import_records else None,
                "project_import_report_json": "source/reports/project_import_report.json" if project_import_records else None,
                "reports": "source/reports/",
                "worktable_changes": "source/worktable_changes.md",
                "worktable_patch": "source/worktable.patch.json",
                "request_spec": "source/request.spec.yaml" if request_spec is not None else None,
                "validation_diff": "source/validation_diff.md" if validation_diff is not None else None,
                "validation_diff_json": "source/validation_diff.json" if validation_diff_json is not None else None,
                "validation_report": "source/reports/validation_report.md",
                "validation_report_json": "source/reports/validation_report.json",
                "metadata": "source/metadata.json",
            },
            "compiled_xscr": "direct-imports/scripts/full-script/generated_script.xscr",
            "subroutines": subroutine_records,
            "hardware": hardware_report if hardware_evidence_present else {},
            "method_touchtools_readiness": method_touchtools_report,
            "project_imports": project_import_records,
            "files": copied_files,
            "manual_recreation": manual_recreation,
        }

        guide_dest = script_dir / "RECREATE_SCRIPT.md"
        if _write_recreate_from_ir(
            protocol_ir_dest,
            guide_dest,
            worklist_present=worklist_present,
            project_archive_present=bool(project_import_records),
            request_spec_present=request_spec is not None,
        ):
            exports.append(ExportedArtifact(guide_dest, guide_dest, "recreate-guide"))
            copied_files.append(_file_record("recreate-guide", guide_dest, guide_dest, bundle_root=bundle_root))
        else:
            _write_recreate_unavailable(protocol_ir_dest, guide_dest)
            exports.append(ExportedArtifact(guide_dest, guide_dest, "recreate-guide"))
            copied_files.append(_file_record("recreate-guide", guide_dest, guide_dest, bundle_root=bundle_root))

        copied_files.append(_file_record("metadata", metadata_dest, metadata_dest, bundle_root=bundle_root))
        metadata["files"] = copied_files
        ensure_parent(metadata_dest)
        try:
            metadata_dest.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            _raise_bundle_transaction_error(
                "metadata_generation",
                exc,
                detail=f"Could not write staged bundle metadata {metadata_dest}",
            )
        exports.append(ExportedArtifact(metadata_dest, metadata_dest, "metadata"))
    except _ReadyBundleTransactionError as exc:
        verification_state = (
            "failed_validation"
            if exc.failure_point == "post_package_validation"
            else "failed_transaction"
        )
        failed_bundle_path = _quarantine_bundle_transaction_failure(
            script_dir,
            failed_bundle,
            metadata_path=metadata_dest,
            bundle_name=script_name,
            context_name=context_name,
            source_projects=source_projects or [],
            copied_files=copied_files,
            failure_point=exc.failure_point,
            error=exc.original or exc,
            verification_state=verification_state,
            readiness_status=verification_state,
        )
        raise PipelineError(f"{exc}; failed package moved to {failed_bundle_path}") from exc
    except Exception as exc:
        failed_bundle_path = _quarantine_bundle_transaction_failure(
            script_dir,
            failed_bundle,
            metadata_path=metadata_dest,
            bundle_name=script_name,
            context_name=context_name,
            source_projects=source_projects or [],
            copied_files=copied_files,
            failure_point="bundle_assembly",
            error=exc,
        )
        raise PipelineError(f"Could not assemble staged ready bundle: {exc}; failed package moved to {failed_bundle_path}") from exc

    stage = ReadyBundleStage(
        staging_root=staging_run_root,
        script_dir=script_dir,
        bundle_name=final_name,
        final_bundle_dir=published_bundle,
        failed_bundle_dir=failed_bundle,
        validation_report=validation_report,
        verification_state=metadata["verification_state"],
        exports=list(exports),
        metadata_path=metadata_dest,
        protocol_name=script_name,
    )
    if export_summary is not None:
        export_summary.clear()
        export_summary.update(
            {
                "bundle_name": final_name,
                "bundle_dir": script_dir,
                "metadata_path": metadata_dest,
                "final_validation_report": validation_report,
                "readiness_status": readiness_status,
                "readiness": readiness,
                "validation_report_markdown": validation_dest,
                "validation_report_json": validation_json_dest,
            }
        )
    if not publish:
        return stage
    if not validation_report["ready"]:
        failure = PipelineError(validation_failure_message(validation_report))
        failed_bundle_path = _quarantine_bundle_transaction_failure(
            script_dir,
            failed_bundle,
            metadata_path=metadata_dest,
            bundle_name=script_name,
            context_name=context_name,
            source_projects=source_projects or [],
            copied_files=copied_files,
            failure_point="post_package_validation",
            error=failure,
            verification_state="failed_validation",
            readiness_status="failed_validation",
        )
        raise PipelineError(f"{failure}; failed package moved to {failed_bundle_path}") from failure
    try:
        return publish_ready_to_import_zeia(stage)
    except Exception as exc:
        failed_bundle_path = _quarantine_bundle_transaction_failure(
            script_dir,
            failed_bundle,
            metadata_path=metadata_dest,
            bundle_name=script_name,
            context_name=context_name,
            source_projects=source_projects or [],
            copied_files=copied_files,
            failure_point="final_publication",
            error=exc,
        )
        raise PipelineError(f"{exc}; failed package moved to {failed_bundle_path}") from exc


def _write_bundle_transaction_failure_metadata(
    metadata_path: Path,
    *,
    bundle_name: str,
    context_name: str | None,
    source_projects: list[Path],
    copied_files: list[dict[str, str]],
    failure_point: str,
    error: BaseException,
    failed_bundle_dir: Path,
    verification_state: str = "failed_transaction",
    readiness_status: str | None = None,
) -> None:
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            metadata = loaded

    lifecycle = metadata.get("lifecycle") if isinstance(metadata.get("lifecycle"), dict) else {}
    created_from = lifecycle.get("created_from") if isinstance(lifecycle.get("created_from"), dict) else {}
    if not created_from:
        created_from = created_from_record(
            context_name=context_name,
            source_projects=source_projects,
        )
    source_kind = (
        lifecycle.get("source_export_kind")
        or metadata.get("source_export_kind")
        or "unknown"
    )
    exported_at = metadata.get("exported_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    readiness = metadata.get("readiness") if isinstance(metadata.get("readiness"), dict) else {}
    layout = metadata.get("layout") if isinstance(metadata.get("layout"), dict) else {}
    layout.setdefault("source", "source/")
    layout["metadata"] = "source/metadata.json"
    files = metadata.get("files") if isinstance(metadata.get("files"), list) else list(copied_files)
    failure_record = {
        "stage": failure_point,
        "error_type": error.__class__.__name__,
        "message": str(error),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quarantine_destination": str(failed_bundle_dir),
    }
    failure_history = metadata.get("transaction_failures")
    if not isinstance(failure_history, list):
        failure_history = []
    failure_history.append(failure_record)
    normalized_lifecycle = lifecycle_metadata(
        bundle_role="debug",
        source_export_kind=str(source_kind),
        verification_state=verification_state,
        created_from=created_from,
        supersedes=metadata.get("supersedes"),
        superseded_by=metadata.get("superseded_by"),
    )
    metadata.update(
        {
            "bundle_schema_version": metadata.get("bundle_schema_version") or BUNDLE_SCHEMA_VERSION,
            "script_name": metadata.get("script_name") or bundle_name,
            "context_name": metadata.get("context_name") if metadata.get("context_name") is not None else context_name,
            "exported_at": exported_at,
            "bundle_role": "debug",
            "ready_to_import": False,
            "source_export_kind": str(source_kind),
            "verification_state": verification_state,
            "readiness_status": readiness_status or "failed_transaction",
            "readiness": readiness,
            "supersedes": normalized_lifecycle["supersedes"],
            "superseded_by": normalized_lifecycle["superseded_by"],
            "lifecycle": normalized_lifecycle,
            "layout": layout,
            "files": files,
            "transaction_failure": failure_record,
            "transaction_failures": failure_history,
        }
    )
    ensure_parent(metadata_path)
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _quarantine_bundle_transaction_failure(
    bundle_root: Path,
    failed_bundle_dir: Path,
    *,
    metadata_path: Path,
    bundle_name: str,
    context_name: str | None,
    source_projects: list[Path],
    copied_files: list[dict[str, str]],
    failure_point: str,
    error: BaseException,
    verification_state: str = "failed_transaction",
    readiness_status: str | None = None,
) -> Path:
    bundle_root.mkdir(parents=True, exist_ok=True)
    _write_bundle_transaction_failure_metadata(
        metadata_path,
        bundle_name=bundle_name,
        context_name=context_name,
        source_projects=source_projects,
        copied_files=copied_files,
        failure_point=failure_point,
        error=error,
        failed_bundle_dir=failed_bundle_dir,
        verification_state=verification_state,
        readiness_status=readiness_status,
    )
    _move_bundle_root(bundle_root, failed_bundle_dir)
    _cleanup_empty_directory(bundle_root.parent)
    return failed_bundle_dir


def _publish_bundle_replacement(
    staged_bundle: Path,
    published_bundle: Path,
    *,
    backup_bundle: Path,
) -> None:
    ensure_parent(backup_bundle)
    try:
        _replace_path_with_retry(published_bundle, backup_bundle)
    except OSError as exc:
        raise PipelineError(
            f"Could not prepare published bundle {published_bundle} for replacement: {exc}"
        ) from exc
    try:
        _replace_path_with_retry(staged_bundle, published_bundle)
    except OSError as exc:
        try:
            _replace_path_with_retry(backup_bundle, published_bundle)
        except OSError as restore_exc:
            raise PipelineError(
                f"Could not publish staged replacement from {staged_bundle} to {published_bundle}: {exc}; "
                f"restore from {backup_bundle} also failed: {restore_exc}"
            ) from restore_exc
        raise PipelineError(
            f"Could not publish staged replacement from {staged_bundle} to {published_bundle}: {exc}"
        ) from exc


def _replace_path_with_retry(source: Path, destination: Path, *, attempts: int = 12) -> None:
    """Atomically replace a path, tolerating transient Windows file locks."""

    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.5)
    assert last_error is not None
    raise last_error


def publish_ready_to_import_bundle(stage: ReadyBundleStage) -> list[ExportedArtifact]:
    """Deprecated staging-folder publish.

    Do not use for handoff. Delivery bundles must go through
    ``publish_ready_to_import_zeia`` (``export_ready_to_import`` publish=True already
    routes there) so ready-to-import roots are ``<name>_vN/<name>_vN.zeia`` plus the
    universal ``run_tecan_bundle_setup.bat``.
    """
    raise PipelineError(
        "publish_ready_to_import_bundle is removed. Use publish_ready_to_import_zeia "
        "(or export_ready_to_import with publish=True) so delivery folders publish as "
        "<name>_vN/<name>_vN.zeia with run_tecan_bundle_setup.bat."
    )


def publish_ready_to_import_zeia(stage: ReadyBundleStage) -> list[ExportedArtifact]:
    """Publish validated generated ZEIA archives as complete protocol delivery folders."""
    if not stage.validation_report["ready"]:
        raise PipelineError("The ZEIA did not pass strict readiness validation.")

    zeia_artifacts = [
        artifact
        for artifact in stage.exports
        if artifact.kind == "generated-project-archive"
        and artifact.destination.suffix.lower() == ".zeia"
        and artifact.destination.exists()
    ]
    if not zeia_artifacts:
        raise PipelineError("Only generated ZEIA archives may be published to ready-to-import.")

    ready_root = READY_TO_IMPORT_DIR
    ready_root.mkdir(parents=True, exist_ok=True)
    published: list[ExportedArtifact] = []
    published_ok = False
    try:
        for index, artifact in enumerate(zeia_artifacts, start=1):
            protocol_base = _ready_protocol_folder_base_name(stage.protocol_name or stage.bundle_name, index)
            publish_plan = plan_ready_to_import_publish(ready_root, protocol_base)
            protocol_folder = publish_plan.bundle_name
            destination_dir = publish_plan.bundle_dir
            destination = publish_plan.archive_path
            staged_dir = publish_plan.staging_dir
            if staged_dir.exists():
                shutil.rmtree(staged_dir)
            backup_dir = publish_plan.backup_dir
            try:
                _assemble_protocol_delivery_folder(
                    stage,
                    zeia_artifact=artifact,
                    staged_dir=staged_dir,
                    protocol_folder=protocol_folder,
                )
                _publish_protocol_folder_replacement(
                    staged_dir,
                    destination_dir,
                    backup_dir=backup_dir,
                )
                _validate_published_protocol_folder(destination_dir, protocol_folder, require_final_reports=False)
            except Exception:
                if staged_dir.exists():
                    shutil.rmtree(staged_dir, ignore_errors=True)
                raise
            _remove_legacy_loose_zeia(ready_root, protocol_folder)
            if protocol_base != protocol_folder:
                _remove_legacy_loose_zeia(ready_root, protocol_base)
            published.append(ExportedArtifact(artifact.destination, destination, "fluent-project-archive"))
        published_ok = True
        return published
    finally:
        if published_ok:
            shutil.rmtree(stage.staging_root, ignore_errors=True)


def _assemble_protocol_delivery_folder(
    stage: ReadyBundleStage,
    *,
    zeia_artifact: ExportedArtifact,
    staged_dir: Path,
    protocol_folder: str,
) -> None:
    staged_dir.mkdir(parents=True)
    published_zeia = staged_dir / f"{protocol_folder}.zeia"
    _copy(zeia_artifact.destination, published_zeia)

    required_files = [
        (stage.script_dir / "RECREATE_SCRIPT.md", staged_dir / "RECREATE_SCRIPT.md", "recreate instructions"),
        (
            stage.script_dir / "source" / "request.spec.yaml",
            staged_dir / "source" / "request.spec.yaml",
            "request specification",
        ),
        (
            stage.script_dir / "source" / "protocol.ir.json",
            staged_dir / "source" / "protocol.ir.json",
            "protocol IR",
        ),
        (
            stage.script_dir / "source" / "protocol_draft.py",
            staged_dir / "source" / "generated" / "protocol.py",
            "generated Python",
        ),
    ]
    missing = [label for source, _destination, label in required_files if not source.exists()]
    reports_source = stage.script_dir / "source" / "reports"
    if not reports_source.exists():
        missing.append("reports directory")
    if missing:
        raise PipelineError(
            "Cannot publish a complete protocol delivery folder; missing "
            + ", ".join(missing)
            + f" in staged bundle {stage.script_dir}"
        )

    for source, destination, _label in required_files:
        _copy(source, destination)

    _copy_v2_source_tree(stage.script_dir / "source", staged_dir / "source")
    _copy_delivery_optional(stage.metadata_path, staged_dir / "source" / "metadata.json")
    _copy_delivery_optional(stage.script_dir / "RECREATE_SCRIPT.md", staged_dir / "source" / "RECREATE_SCRIPT.md")
    _copy_delivery_optional(stage.script_dir / "RECIPE_GROUP_NOTES.md", staged_dir / "RECIPE_GROUP_NOTES.md")
    _copy_delivery_optional(stage.script_dir / "RECIPE_GROUP_NOTES.md", staged_dir / "source" / "RECIPE_GROUP_NOTES.md")
    _copy_delivery_optional(stage.script_dir / "source" / "RECIPE_GROUP_NOTES.md", staged_dir / "RECIPE_GROUP_NOTES.md")
    _copy_delivery_optional(
        stage.script_dir / "source" / "RECIPE_GROUP_NOTES.md",
        staged_dir / "source" / "RECIPE_GROUP_NOTES.md",
    )

    media_dir = staged_dir / "media"
    extract_archive_filesystem_payloads(published_zeia, media_dir)
    organize_bundle_touchtools_media(media_dir, staged_dir / "source")
    _write_touchtools_deploy_config(staged_dir / "source", protocol_folder)
    external_file_deployments = _stage_external_file_deployments(
        published_zeia,
        bundle_dir=staged_dir,
    )
    _copy_v2_setup_script(staged_dir / "run_tecan_bundle_setup.bat")
    _write_delivery_manifest(
        stage,
        staged_dir=staged_dir,
        protocol_folder=protocol_folder,
        external_file_deployments=external_file_deployments,
    )
    _validate_published_protocol_folder(staged_dir, protocol_folder, require_final_reports=False)


def _copy_v2_source_tree(source_dir: Path, destination_dir: Path) -> None:
    """Copy the accepted V2 companion tree without importable/intermediate artifacts."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    excluded_directories = {"original-sources"}
    excluded_suffixes = {".xscr", ".zeia"}
    for source in source_dir.rglob("*"):
        relative = source.relative_to(source_dir)
        if any(part.casefold() in excluded_directories for part in relative.parts):
            continue
        if source.is_dir():
            (destination_dir / relative).mkdir(parents=True, exist_ok=True)
            continue
        if source.suffix.casefold() in excluded_suffixes:
            continue
        _copy(source, destination_dir / relative)


def _write_touchtools_deploy_config(source_dir: Path, protocol_folder: str) -> None:
    write_json(
        source_dir / "touchtools_deploy.json",
        {
            "schema_version": "tecan.touchtools_deploy.v1",
            "media_subfolder": f"{protocol_folder}_media",
            "deploy_source": "media/processed",
        },
    )


def _stage_external_file_deployments(
    archive_path: Path,
    *,
    bundle_dir: Path,
) -> list[dict[str, str]]:
    """Stage non-TouchTools ZEIA filesystem payloads with exact deployment metadata."""
    destination_root = bundle_dir / "source" / "external-files"
    if destination_root.exists():
        shutil.rmtree(destination_root)

    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        return []

    records: list[dict[str, str]] = []
    targets: dict[str, tuple[str, str]] = {}
    with zipfile.ZipFile(archive_path, "r") as archive:
        mapping_name = next(
            (
                name
                for name in archive.namelist()
                if name.replace("\\", "/").casefold() == "fs/mapping.xml"
            ),
            None,
        )
        if mapping_name is None:
            return []
        directories = {
            key: directory
            for key, directory in parse_fs_mapping_directories(archive.read(mapping_name))
        }
        for info in sorted(archive.infolist(), key=lambda item: item.filename.casefold()):
            normalized = info.filename.replace("\\", "/")
            parts = PurePosixPath(normalized).parts
            if info.is_dir() or len(parts) < 3 or parts[0].casefold() != "fs":
                continue
            try:
                fs_key = int(parts[1])
            except ValueError:
                continue
            target_root = directories.get(fs_key)
            if not target_root:
                raise PipelineError(f"ZEIA filesystem payload has no directory mapping: {normalized}")
            relative_parts = parts[2:]
            if any(part in {"", ".", ".."} for part in relative_parts):
                raise PipelineError(f"ZEIA filesystem payload has an unsafe archive path: {normalized}")
            target_path = str(PureWindowsPath(target_root, *relative_parts))
            if _is_touchtools_media_target(target_path):
                continue

            payload = archive.read(info.filename)
            digest = hashlib.sha256(payload).hexdigest()
            target_key = target_path.casefold()
            previous = targets.get(target_key)
            if previous is not None:
                previous_entry, previous_digest = previous
                if previous_digest != digest:
                    raise PipelineError(
                        f"Conflicting ZEIA filesystem payloads target {target_path}: "
                        f"{previous_entry} and {normalized}"
                    )
                continue
            targets[target_key] = (normalized, digest)

            destination = destination_root / str(fs_key) / Path(*relative_parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            records.append(
                {
                    "bundle_path": destination.relative_to(bundle_dir).as_posix(),
                    "target_path": target_path,
                    "sha256": digest,
                }
            )
    return records


def _is_touchtools_media_target(target_path: str) -> bool:
    normalized = str(target_path or "").replace("/", "\\").casefold()
    return "\\tecan\\visionx\\touchtoolsdata\\images" in normalized


def _copy_v2_setup_script(destination: Path) -> None:
    template = Path(__file__).resolve().parents[1] / "tools" / "run_tecan_bundle_setup.bat"
    if not template.is_file():
        raise PipelineError(f"V2 bundle setup template is missing: {template}")
    _copy(template, destination)
    helpers = (
        "collect_tecan_diagnostic_bundle.ps1",
        "copy_tree_with_progress.ps1",
        "stall_watchdog.ps1",
        "install_external_files.ps1",
        "deploy_touchtools_media.ps1",
    )
    helper_dir = destination.parent / "source"
    helper_dir.mkdir(parents=True, exist_ok=True)
    for helper_name in helpers:
        helper = template.with_name(helper_name)
        if not helper.is_file():
            raise PipelineError(f"Tecan bundle setup helper is missing: {helper}")
        _copy(helper, helper_dir / helper.name)


def _copy_directory_contents(source_dir: Path, destination_dir: Path) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        destination = destination_dir / item.name
        if item.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(item, destination)
        elif item.is_file():
            _copy(item, destination)


def _copy_delivery_optional(source: Path, destination: Path) -> None:
    if source.exists() and source.is_file():
        _copy(source, destination)


def _write_delivery_manifest(
    stage: ReadyBundleStage,
    *,
    staged_dir: Path,
    protocol_folder: str,
    external_file_deployments: list[dict[str, str]],
) -> None:
    delivery_manifest = {
        "schema_version": DELIVERY_MANIFEST_SCHEMA_VERSION,
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "protocol_name": protocol_folder,
        "external_file_deployments": external_file_deployments,
        "deliverables": [
            {
                "kind": "fluent_project_archive",
                "path": f"{protocol_folder}.zeia",
                "description": "Only FluentControl import deliverable in this folder.",
            }
        ],
        "companion_artifacts": [
            {"kind": "recreation_instructions", "path": "RECREATE_SCRIPT.md"},
            {"kind": "request_specification", "path": "source/request.spec.yaml"},
            {"kind": "protocol_ir", "path": "source/protocol.ir.json"},
            {"kind": "generated_python", "path": "source/generated/protocol.py"},
            {"kind": "reports", "path": "source/reports/"},
            {"kind": "bundle_metadata", "path": "source/metadata.json"},
            {"kind": "v2_source_tree", "path": "source/"},
            {"kind": "touchtools_media", "path": "media/"},
            {"kind": "bundle_setup", "path": "run_tecan_bundle_setup.bat"},
            {"kind": "bundle_setup_helpers", "path": "source/"},
        ],
        "internal_artifacts": [
            {
                "kind": "compiled_xscr_intermediate",
                "published": False,
                "description": "Compiled XSCR remains an internal compilation artifact and is not copied into this delivery folder.",
            }
        ],
        "source_staging_bundle": str(stage.script_dir),
    }
    write_json(staged_dir / "source" / "delivery_manifest.json", delivery_manifest)


def attach_generation_reports_to_protocol_folders(
    artifact_paths: list[str | Path],
    *,
    ready_root: Path,
    generation_manifest: Path,
    workflow_report: Path,
    companion_files: dict[str, Path] | None = None,
) -> list[ExportedArtifact]:
    """Atomically attach final generation reports to published protocol folders."""
    if not generation_manifest.exists() or not workflow_report.exists():
        return []

    attached: list[ExportedArtifact] = []
    for protocol_dir in _bundle_dirs_from_artifacts(artifact_paths, ready_root=ready_root):
        attached.extend(
            attach_generation_reports_to_protocol_folder(
                protocol_dir,
                generation_manifest=generation_manifest,
                workflow_report=workflow_report,
                companion_files=companion_files or {},
            )
        )
    return attached


def attach_generation_reports_to_protocol_folder(
    protocol_dir: Path,
    *,
    generation_manifest: Path,
    workflow_report: Path,
    companion_files: dict[str, Path] | None = None,
) -> list[ExportedArtifact]:
    """Attach final generation reports to one protocol delivery folder via atomic replacement."""
    if not generation_manifest.exists() or not workflow_report.exists():
        return []

    ready_root = protocol_dir.parent
    run_id = _package_run_id()
    staged_dir = ready_root / f".{protocol_dir.name}.{run_id}.staging"
    backup_dir = ready_root / f".{protocol_dir.name}.{run_id}.backup"
    if staged_dir.exists():
        shutil.rmtree(staged_dir)
    shutil.copytree(protocol_dir, staged_dir)
    artifacts = [
        ExportedArtifact(
            generation_manifest,
            staged_dir / "source" / "generation_manifest.json",
            "generation-manifest",
        ),
        ExportedArtifact(
            workflow_report,
            staged_dir / "source" / "GENERATION_WORKFLOW.md",
            "workflow-report",
        ),
    ]
    try:
        _copy(generation_manifest, staged_dir / "source" / "generation_manifest.json")
        _copy(workflow_report, staged_dir / "source" / "GENERATION_WORKFLOW.md")
        for relative, source in (companion_files or {}).items():
            if not source.exists() or source.suffix.lower() == ".xscr":
                continue
            relative_path = Path(relative)
            destination = (
                staged_dir / relative_path
                if relative_path.as_posix() in {"RECREATE_SCRIPT.md", "RECIPE_GROUP_NOTES.md"}
                else staged_dir / "source" / relative_path
            )
            _copy(source, destination)
            artifacts.append(ExportedArtifact(source, destination, _delivery_artifact_kind(relative)))
        _refresh_delivery_manifest(staged_dir)
        _validate_published_protocol_folder(staged_dir, protocol_dir.name, require_final_reports=True)
        _publish_protocol_folder_replacement(staged_dir, protocol_dir, backup_dir=backup_dir)
        return _retarget_exported_artifacts(artifacts, from_root=staged_dir, to_root=protocol_dir)
    except Exception as exc:
        if staged_dir.exists():
            shutil.rmtree(staged_dir, ignore_errors=True)
        raise PipelineError(f"Could not attach generation reports to {protocol_dir}: {exc}") from exc


def _delivery_artifact_kind(relative: str) -> str:
    name = Path(relative).name.lower()
    if name.endswith(".json"):
        return "delivery-report-json"
    if name.endswith(".md"):
        return "delivery-report"
    if name.endswith(".yaml") or name.endswith(".yml"):
        return "request-spec"
    if name.endswith(".py"):
        return "generated-python"
    return "companion-artifact"


def _refresh_delivery_manifest(staged_dir: Path) -> None:
    manifest_path = staged_dir / "source" / "delivery_manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    manifest.setdefault("schema_version", DELIVERY_MANIFEST_SCHEMA_VERSION)
    manifest.setdefault("bundle_schema_version", BUNDLE_SCHEMA_VERSION)
    manifest.setdefault("protocol_name", staged_dir.name)
    manifest.setdefault(
        "deliverables",
        [
            {
                "kind": "fluent_project_archive",
                "path": f"{staged_dir.name}.zeia",
                "description": "Only FluentControl import deliverable in this folder.",
            }
        ],
    )
    manifest.setdefault("internal_artifacts", [{"kind": "compiled_xscr_intermediate", "published": False}])
    companions = manifest.setdefault("companion_artifacts", [])
    existing = {item.get("path") for item in companions if isinstance(item, dict)}
    for kind, relative in (
        ("generation_manifest", "source/generation_manifest.json"),
        ("workflow_report", "source/GENERATION_WORKFLOW.md"),
    ):
        if relative not in existing:
            companions.append({"kind": kind, "path": relative})
    write_json(manifest_path, manifest)


def _validate_published_protocol_folder(
    protocol_dir: Path,
    protocol_folder: str | None = None,
    *,
    require_final_reports: bool,
) -> None:
    result = validate_v2_delivery_bundle(
        protocol_dir,
        protocol_name=protocol_folder or protocol_dir.name,
        require_final_reports=require_final_reports,
    )
    if not result.ok:
        raise PipelineError(delivery_bundle_failure_message(result))


def _ready_protocol_folder_base_name(base_name: str, index: int = 1) -> str:
    stem = _safe_label(Path(base_name).stem)
    if index > 1:
        stem = f"{stem}_artifact{index}"
    return stem


def _publish_protocol_folder_replacement(
    staged_dir: Path,
    published_dir: Path,
    *,
    backup_dir: Path,
) -> None:
    ensure_parent(published_dir)
    backup_created = False
    if published_dir.exists():
        try:
            _move_protocol_directory(published_dir, backup_dir)
            backup_created = True
        except OSError as exc:
            raise PipelineError(
                f"Could not prepare published protocol folder {published_dir} for replacement: {exc}"
            ) from exc
    try:
        _move_protocol_directory(staged_dir, published_dir)
    except OSError as exc:
        if backup_created:
            try:
                _move_protocol_directory(backup_dir, published_dir)
            except OSError as restore_exc:
                raise PipelineError(
                    f"Could not publish staged protocol folder from {staged_dir} to {published_dir}: {exc}; "
                    f"restore from {backup_dir} also failed: {restore_exc}"
                ) from restore_exc
        raise PipelineError(
            f"Could not publish staged protocol folder from {staged_dir} to {published_dir}: {exc}"
        ) from exc
    if backup_created:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _move_protocol_directory(source: Path, destination: Path) -> None:
    last_error: OSError | None = None
    for attempt in range(12):
        if destination.exists() and source.exists():
            if destination.is_dir():
                shutil.rmtree(destination, ignore_errors=True)
            else:
                destination.unlink()
        try:
            _move_bundle_root(source, destination)
            return
        except PipelineError:
            raise
        except OSError as exc:
            last_error = exc
            try:
                shutil.move(str(source), str(destination))
                return
            except OSError as move_exc:
                last_error = move_exc
                if attempt < 11:
                    time.sleep(0.5)
    if last_error is not None:
        raise last_error


def _remove_legacy_loose_zeia(root: Path, protocol_folder: str) -> None:
    legacy = root / f"{protocol_folder}.zeia"
    if legacy.exists() and legacy.is_file():
        legacy.unlink()


def cleanup_ready_to_import_stage(stage: ReadyBundleStage) -> None:
    """Discard a staged bundle that was not published."""
    shutil.rmtree(stage.staging_root, ignore_errors=True)


def attach_generation_reports_to_ready_bundles(
    artifact_paths: list[str | Path],
    *,
    ready_root: Path,
    generation_manifest: Path,
    workflow_report: Path,
) -> list[ExportedArtifact]:
    """Attach finalized workflow reports to ready bundles created by this run."""
    if not generation_manifest.exists() or not workflow_report.exists():
        return []

    attached: list[ExportedArtifact] = []
    for bundle_dir in _bundle_dirs_from_artifacts(artifact_paths, ready_root=ready_root):
        attached.extend(
            attach_generation_reports_to_bundle(
                bundle_dir,
                generation_manifest=generation_manifest,
                workflow_report=workflow_report,
            )
        )
    return attached


def attach_generation_reports_to_bundle(
    bundle_dir: Path,
    *,
    generation_manifest: Path,
    workflow_report: Path,
) -> list[ExportedArtifact]:
    """Attach finalized workflow reports to a specific staged or published bundle."""
    if not generation_manifest.exists() or not workflow_report.exists():
        return []

    run_id = _package_run_id()
    staging_root = PACKAGE_STAGING_DIR / run_id
    staged_bundle = staging_root / bundle_dir.name
    failed_bundle = FAILED_PACKAGES_DIR / run_id / bundle_dir.name
    backup_bundle = staging_root / f"{bundle_dir.name}.backup"
    shutil.copytree(bundle_dir, staged_bundle)

    try:
        source_dir = staged_bundle / "source"
        manifest_dest = source_dir / "generation_manifest.json"
        workflow_dest = source_dir / "GENERATION_WORKFLOW.md"
        _copy(generation_manifest, manifest_dest)
        _copy(workflow_report, workflow_dest)
        bundle_artifacts = [
            ExportedArtifact(generation_manifest, manifest_dest, "generation-manifest"),
            ExportedArtifact(workflow_report, workflow_dest, "workflow-report"),
        ]
        _record_attached_generation_reports(staged_bundle, bundle_artifacts, bundle_root=staged_bundle)
        _publish_bundle_replacement(
            staged_bundle,
            bundle_dir,
            backup_bundle=backup_bundle,
        )
        shutil.rmtree(backup_bundle, ignore_errors=True)
        return _retarget_exported_artifacts(
            bundle_artifacts,
            from_root=staged_bundle,
            to_root=bundle_dir,
        )
    except Exception as exc:
        if backup_bundle.exists() and not bundle_dir.exists():
            os.replace(backup_bundle, bundle_dir)
        if staged_bundle.exists():
            _quarantine_bundle_transaction_failure(
                staged_bundle,
                failed_bundle,
                metadata_path=staged_bundle / "source" / "metadata.json",
                bundle_name=bundle_dir.name,
                context_name=None,
                source_projects=[],
                copied_files=[],
                failure_point="generation_manifest_creation",
                error=exc,
            )
        raise PipelineError(f"Could not attach generation reports to {bundle_dir}: {exc}") from exc
    finally:
        _cleanup_empty_directory(staging_root)


def _bundle_dirs_from_artifacts(artifact_paths: list[str | Path], *, ready_root: Path) -> list[Path]:
    bundle_dirs: list[Path] = []
    seen: set[Path] = set()
    ready_root = ready_root.resolve()
    for value in artifact_paths:
        path = Path(value)
        try:
            relative = path.resolve().relative_to(ready_root)
        except ValueError:
            continue
        if not relative.parts:
            continue
        bundle_dir = ready_root / relative.parts[0]
        if bundle_dir in seen:
            continue
        seen.add(bundle_dir)
        bundle_dirs.append(bundle_dir)
    return bundle_dirs


def _record_attached_generation_reports(
    bundle_dir: Path,
    artifacts: list[ExportedArtifact],
    *,
    bundle_root: Path,
) -> None:
    metadata_path = bundle_dir / "source" / "metadata.json"
    if not metadata_path.exists():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    layout = metadata.setdefault("layout", {})
    layout["generation_manifest"] = "source/generation_manifest.json"
    layout["workflow_report"] = "source/GENERATION_WORKFLOW.md"

    files = metadata.setdefault("files", [])
    for artifact in artifacts:
        record = _file_record(artifact.kind, artifact.source, artifact.destination, bundle_root=bundle_root)
        files[:] = [
            existing
            for existing in files
            if existing.get("relative_path") != record["relative_path"]
        ]
        files.append(record)
    manifest = _read_attached_generation_manifest(bundle_dir)
    if manifest:
        lifecycle = lifecycle_metadata(
            bundle_role="ready" if manifest.get("ready_to_import") else "debug",
            source_export_kind=source_export_kind(
                manifest.get("full_zeia_export"),
                approved_partial=bool(manifest.get("partial_zeia_export_approved")),
            ),
            verification_state=verification_state_from_readiness(
                ready_to_import=bool(manifest.get("ready_to_import")),
                readiness=manifest.get("readiness") if isinstance(manifest.get("readiness"), dict) else None,
                workflow_status=manifest.get("workflow_status"),
            ),
            created_from=created_from_record(
                context_name=manifest.get("context"),
                context_kind=manifest.get("context_kind"),
                source_contexts=manifest.get("source_contexts") or [],
                source_projects=[],
            ),
        )
        metadata.update(
            {
                "bundle_role": lifecycle["bundle_role"],
                "source_export_kind": lifecycle["source_export_kind"],
                "verification_state": lifecycle["verification_state"],
                "supersedes": lifecycle["supersedes"],
                "superseded_by": lifecycle["superseded_by"],
                "lifecycle": lifecycle,
            }
        )
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def _read_attached_generation_manifest(bundle_dir: Path) -> dict[str, Any]:
    manifest_path = bundle_dir / "source" / "generation_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return manifest if isinstance(manifest, dict) else {}


def audit_ready_bundle(
    bundle_dir: Path,
    *,
    expected_bundle_dir: Path | None = None,
    require_generation_reports: bool = False,
) -> dict[str, Any]:
    """Audit a staged or published ready bundle before publication or handoff."""
    bundle_dir = bundle_dir.resolve()
    source_dir = bundle_dir / "source"
    metadata_path = source_dir / "metadata.json"
    manifest_path = source_dir / "generation_manifest.json"
    workflow_path = source_dir / "GENERATION_WORKFLOW.md"
    expected_bundle_dir = expected_bundle_dir.resolve() if expected_bundle_dir else bundle_dir

    audit: dict[str, Any] = {
        "kind": "ready_bundle_audit",
        "bundle_dir": str(bundle_dir),
        "expected_bundle_dir": str(expected_bundle_dir),
        "status": "passed",
        "summary": "Ready bundle inventory is complete.",
        "blocking": [],
        "needs_review": [],
        "metadata_path": str(metadata_path) if metadata_path.exists() else None,
        "generation_manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "workflow_report_path": str(workflow_path) if workflow_path.exists() else None,
        "inventory_count": 0,
        "missing_inventory": [],
        "missing_files": [],
        "report_inventory": {
            "generation_manifest": False,
            "workflow_report": False,
        },
    }

    if not metadata_path.exists():
        audit["blocking"].append(
            {
                "kind": "metadata_missing",
                "path": str(metadata_path),
                "message": "Ready bundle metadata.json is missing.",
            }
        )
        audit["status"] = "failed"
        audit["summary"] = "Ready bundle metadata is missing."
        return audit

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        audit["blocking"].append(
            {
                "kind": "metadata_invalid",
                "path": str(metadata_path),
                "message": f"Ready bundle metadata.json is not valid JSON: {exc}",
            }
        )
        audit["status"] = "failed"
        audit["summary"] = "Ready bundle metadata is invalid."
        return audit

    files = metadata.get("files") if isinstance(metadata, dict) else []
    if not isinstance(files, list):
        files = []
    audit["inventory_count"] = len(files)

    inventory_paths: set[str] = set()
    for record in files:
        if not isinstance(record, dict):
            continue
        relative_path = str(record.get("relative_path") or "")
        if relative_path:
            inventory_paths.add(relative_path)
            actual = bundle_dir / Path(relative_path)
            if not actual.exists():
                audit["missing_files"].append(relative_path)
                audit["blocking"].append(
                    {
                        "kind": "bundle_file_missing",
                        "path": relative_path,
                        "message": f"Bundle file is missing: {relative_path}",
                    }
                )

    report_manifest_relative = "source/generation_manifest.json"
    report_workflow_relative = "source/GENERATION_WORKFLOW.md"
    audit["report_inventory"] = {
        "generation_manifest": report_manifest_relative in inventory_paths,
        "workflow_report": report_workflow_relative in inventory_paths,
    }

    if require_generation_reports:
        for relative_path, label in (
            (report_manifest_relative, "generation manifest"),
            (report_workflow_relative, "workflow report"),
        ):
            actual = bundle_dir / Path(relative_path)
            if not actual.exists():
                audit["blocking"].append(
                    {
                        "kind": f"{label.replace(' ', '_')}_missing",
                        "path": str(actual),
                        "message": f"Ready bundle is missing the final {label}.",
                    }
                )
                audit["missing_inventory"].append(relative_path)

    if expected_bundle_dir is not None:
        manifest = _read_attached_generation_manifest(bundle_dir)
        if manifest:
            packaged_bundle_dir = str(manifest.get("packaged_bundle_dir") or "")
            expected_text = str(expected_bundle_dir)
            packaged_resolved = (
                Path(packaged_bundle_dir).expanduser().resolve()
                if packaged_bundle_dir
                else None
            )
            if packaged_resolved != expected_bundle_dir:
                audit["blocking"].append(
                    {
                        "kind": "packaged_bundle_dir_mismatch",
                        "path": str(manifest_path),
                        "message": (
                            "generation_manifest.json points at a different final bundle directory "
                            f"({packaged_bundle_dir!r}) than expected ({expected_text!r})."
                        ),
                    }
                )
            ready_artifacts = manifest.get("ready_to_import_artifacts") or []
            if not isinstance(ready_artifacts, list):
                ready_artifacts = []
            if require_generation_reports:
                required_relatives = {
                    (expected_bundle_dir / report_manifest_relative).resolve(),
                    (expected_bundle_dir / report_workflow_relative).resolve(),
                }
                manifest_artifacts = set()
                for value in ready_artifacts:
                    if isinstance(value, (str, Path)) and str(value).strip():
                        try:
                            manifest_artifacts.add(Path(str(value)).expanduser().resolve())
                        except OSError:
                            continue
                missing_artifacts = sorted(
                    str(path) for path in required_relatives - manifest_artifacts
                )
                if missing_artifacts:
                    audit["blocking"].append(
                        {
                            "kind": "manifest_artifact_missing",
                            "path": str(manifest_path),
                            "message": (
                                "generation_manifest.json does not list the final generation "
                                f"artifacts: {', '.join(missing_artifacts)}"
                            ),
                        }
                    )
        elif require_generation_reports:
            audit["blocking"].append(
                {
                    "kind": "generation_manifest_missing",
                    "path": str(manifest_path),
                    "message": "Ready bundle is missing generation_manifest.json.",
                }
            )
        if require_generation_reports and not workflow_path.exists():
            audit["blocking"].append(
                {
                    "kind": "workflow_report_missing",
                    "path": str(workflow_path),
                    "message": "Ready bundle is missing GENERATION_WORKFLOW.md.",
                }
            )

    if audit["blocking"]:
        audit["status"] = "failed"
        audit["summary"] = "Ready bundle audit found blocking issues."
    return audit


def _copy(source: Path, destination: Path) -> None:
    ensure_parent(destination)
    shutil.copy2(source, destination)


def _copy_record(
    source: Path,
    destination: Path,
    kind: str,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
    *,
    bundle_root: Path,
) -> None:
    _copy(source, destination)
    exports.append(ExportedArtifact(source, destination, kind))
    copied_files.append(_file_record(kind, source, destination, bundle_root=bundle_root))


def _reset_strict_bundle(script_dir: Path) -> None:
    for relative in (
        "protocol.ir.json",
        "protocol_draft.py",
        "generated_script.xscr",
        "generated_worklist.gwl",
        "worktable_changes.md",
        "worktable.patch.json",
        "request.spec.yaml",
        "validation_diff.md",
        "validation_diff.json",
        "RECREATE_SCRIPT.md",
        "HARDWARE_PINS.md",
        "METHOD_TOUCHTOOLS_READINESS.md",
        "metadata.json",
    ):
        target = script_dir / relative
        if target.exists() and target.is_file():
            target.unlink()
    for relative in ("direct-imports", "source", "reports", "original_sources", "original-sources", "subroutines", "hardware"):
        target = script_dir / relative
        if target.exists() and target.is_dir():
            shutil.rmtree(target)
    subroutine_manifest = script_dir / "SUBROUTINES.md"
    if subroutine_manifest.exists() and subroutine_manifest.is_file():
        subroutine_manifest.unlink()


def _minimal_harness_request_spec_yaml(protocol_name: str) -> str:
    """Stub request.spec.yaml so Path A assemble can publish harness builders."""
    safe_name = _safe_label(protocol_name)
    return (
        "schema_version: tecan.request_spec.v1\n"
        "request:\n"
        f"  intent: Hand-built harness for {safe_name}.\n"
        f"  protocol_name: {safe_name}\n"
        "generation:\n"
        "  prompt_only: true\n"
        "acceptance:\n"
        "  required_checks: []\n"
    )


def _safe_label(value: str) -> str:
    keep = []
    for char in value:
        if char.isalnum() or char in {".", "_", "-"}:
            keep.append(char)
        else:
            keep.append("-")
    label = "".join(keep).strip(".-_")
    return label or "global"


def _split_versioned_folder_label(label: str) -> tuple[str, int | None]:
    match = _VERSIONED_FOLDER_RE.fullmatch(label)
    if match is None:
        return label, None
    return match.group("base"), int(match.group("version"))


def _package_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def next_ready_bundle_name(root: Path, base_name: str) -> str:
    """Return the next versioned ready-to-import bundle name for a protocol family."""
    return _next_versioned_bundle_name(root, base_name)


def plan_ready_to_import_publish(root: Path, base_name: str, *, run_id: str | None = None) -> ReadyBundlePublishPlan:
    """Reserve versioned ready-to-import paths for a single publish attempt."""
    ready_root = root.resolve()
    bundle_name = next_ready_bundle_name(ready_root, base_name)
    publish_run_id = run_id or _package_run_id()
    bundle_dir = ready_root / bundle_name
    return ReadyBundlePublishPlan(
        ready_root=ready_root,
        base_name=_safe_label(Path(base_name).stem),
        bundle_name=bundle_name,
        bundle_dir=bundle_dir,
        staging_dir=ready_root / f".{bundle_name}.{publish_run_id}.staging",
        backup_dir=ready_root / f".{bundle_name}.{publish_run_id}.backup",
        archive_path=bundle_dir / f"{bundle_name}.zeia",
    )


def _next_available_bundle_name(root: Path, base_name: str) -> str:
    return next_ready_bundle_name(root, base_name)


def _next_versioned_bundle_name(root: Path, base_name: str) -> str:
    requested_label = _safe_label(Path(base_name).stem)
    family_base, requested_version = _split_versioned_folder_label(requested_label)
    pattern = re.compile(rf"^{re.escape(family_base)}(?:_v(\d+))?$", re.IGNORECASE)
    highest_version = 0
    if root.exists():
        for child in root.iterdir():
            if child.is_dir():
                candidate_name = child.name
            elif child.is_file() and child.suffix.lower() == ".zeia":
                candidate_name = child.stem
            else:
                continue
            match = pattern.fullmatch(candidate_name)
            if match is None:
                continue
            highest_version = max(highest_version, int(match.group(1) or 1))
    next_version = max(highest_version + 1 if highest_version else 1, requested_version or 1)
    return f"{family_base}_v{next_version}"


def _retarget_path(value: str | Path, *, from_root: Path, to_root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        return path
    try:
        relative = path.resolve().relative_to(from_root.resolve())
    except (OSError, ValueError):
        return path
    return to_root / relative


def _retarget_exported_artifacts(
    artifacts: list[ExportedArtifact],
    *,
    from_root: Path,
    to_root: Path,
) -> list[ExportedArtifact]:
    updated: list[ExportedArtifact] = []
    for artifact in artifacts:
        updated.append(
            ExportedArtifact(
                source=_retarget_path(artifact.source, from_root=from_root, to_root=to_root),
                destination=_retarget_path(
                    artifact.destination, from_root=from_root, to_root=to_root
                ),
                kind=artifact.kind,
            )
        )
    return updated


def _finalize_bundle_metadata(
    metadata_path: Path,
    *,
    from_root: Path,
    to_root: Path,
    bundle_role: str,
    ready_to_import: bool,
    verification_state: str,
) -> None:
    if not metadata_path.exists():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(metadata, dict):
        return

    metadata["bundle_role"] = bundle_role
    metadata["ready_to_import"] = ready_to_import
    metadata["verification_state"] = verification_state
    lifecycle = metadata.get("lifecycle") if isinstance(metadata.get("lifecycle"), dict) else {}
    metadata["lifecycle"] = {
        **lifecycle,
        "bundle_role": bundle_role,
        "verification_state": verification_state,
    }
    files = metadata.get("files")
    if isinstance(files, list):
        for record in files:
            if not isinstance(record, dict):
                continue
            source = record.get("source")
            if isinstance(source, str):
                record["source"] = str(_retarget_path(source, from_root=from_root, to_root=to_root))
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def _move_bundle_root(source_root: Path, destination_root: Path) -> None:
    source_root = source_root.resolve()
    destination_root = destination_root.resolve()
    ensure_parent(destination_root)
    try:
        os.rename(source_root, destination_root)
    except OSError as exc:
        try:
            shutil.move(str(source_root), str(destination_root))
        except OSError as move_exc:
            raise PipelineError(
                f"Could not publish staged bundle from {source_root} to {destination_root}: {exc}; "
                f"fallback move failed: {move_exc}"
            ) from move_exc


def _cleanup_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _file_record(kind: str, source: Path, destination: Path, *, bundle_root: Path) -> dict[str, str]:
    return {
        "kind": kind,
        "source": str(source),
        "filename": destination.name,
        "relative_path": _bundle_relative_path(destination, bundle_root=bundle_root),
    }


def _bundle_relative_path(destination: Path, *, bundle_root: Path) -> str:
    try:
        relative = destination.resolve().relative_to(bundle_root.resolve())
    except ValueError:
        return destination.name
    return relative.as_posix() if relative.parts else destination.name


def _prepare_generated_touchtools_media(
    protocol_ir_path: Path,
    xscr_path: Path,
    *,
    script_dir: Path,
    source_dir: Path,
    reports_dir: Path,
) -> tuple[dict[str, Any] | None, Path | None]:
    if not protocol_ir_path.exists() or not xscr_path.exists():
        return None, None
    try:
        ir = load_protocol_ir(protocol_ir_path)
    except Exception:
        return None, None
    path_map = build_media_path_map(
        ir,
        resolve_touchtools_images_dir(),
        subfolder=resolve_touchtools_media_subfolder(ir),
    )
    if not path_map.get("entries"):
        return path_map, None

    media_dir = script_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    step_label_assignments = _materialize_step_label_media_into_media_dir(
        ir,
        media_dir,
        source_dir=source_dir,
        script_dir=script_dir,
    )
    media_manifest = organize_bundle_touchtools_media(media_dir, source_dir)
    media_fixups = apply_touchtools_media_path_map_to_xscr(xscr_path, path_map)
    file_refs = collect_file_reference_paths(path_map, ir, None)
    file_ref_fixups = ensure_script_file_references(xscr_path, file_refs)
    orphan_file_ref_removals = strip_orphan_touchtools_media_file_references(xscr_path)

    reports_dir.mkdir(parents=True, exist_ok=True)
    write_json(reports_dir / "media_path_map.json", path_map)
    (reports_dir / "media_path_map.md").write_text(render_media_path_map_markdown(path_map), encoding="utf-8")
    write_json(
        reports_dir / "generated_touchtools_media.json",
        {
            "media_path_map": path_map,
            "media_manifest": media_manifest,
            "media_path_fixups": media_fixups,
            "file_reference_fixups": file_ref_fixups,
            "orphan_touchtools_file_reference_removals": orphan_file_ref_removals,
            "step_label_media_assignments": step_label_assignments,
        },
    )
    return path_map, media_dir


def _protocol_name_stems(ir: dict[str, Any] | None) -> set[str]:
    """Normalized protocol stems used to find prior ready-to-import bundles."""
    stems: set[str] = set()
    protocol = ir.get("protocol") if isinstance(ir, dict) else None
    if not isinstance(protocol, dict):
        return stems
    for key in ("name", "requested_name"):
        raw = str(protocol.get(key) or "").strip()
        if not raw:
            continue
        base, _ = split_version_suffix(raw)
        stem = normalize_protocol_stem(base)
        if stem:
            stems.add(stem)
    return stems


def _prior_ready_bundles_for_protocol(ir: dict[str, Any] | None) -> list[Path]:
    """Newest-first ready-to-import folders whose stem matches the protocol family."""
    stems = _protocol_name_stems(ir)
    ready_root = READY_TO_IMPORT_DIR
    if not stems or not ready_root.is_dir():
        return []
    matches: list[tuple[int, str, Path]] = []
    for bundle in ready_root.iterdir():
        if not bundle.is_dir() or bundle.name.startswith("."):
            continue
        folder_base, version = _split_versioned_folder_label(bundle.name)
        folder_stem = normalize_protocol_stem(folder_base)
        if folder_stem not in stems:
            continue
        matches.append((version if version is not None else 0, bundle.name.casefold(), bundle))
    matches.sort(reverse=True)
    return [path for _, _, path in matches]


def _materialize_step_label_media_into_media_dir(
    ir: dict[str, Any],
    media_dir: Path,
    *,
    source_dir: Path,
    script_dir: Path,
) -> list[dict[str, Any]]:
    """Copy ``stepN.ext`` captures onto underscore slot filenames before placeholders."""
    candidate_roots: list[Path] = [
        source_dir / SOURCE_MEDIA_ORIGINALS_DIR,
        script_dir / "media" / "unprocessed",
        script_dir / "media",
    ]
    # Prior published same-stem bundles often keep real captures under source/media-originals.
    for bundle in _prior_ready_bundles_for_protocol(ir):
        originals = bundle / "source" / SOURCE_MEDIA_ORIGINALS_DIR
        if originals.is_dir():
            candidate_roots.append(originals)
        hardware = bundle / "source" / "hardware" / "assets"
        if hardware.is_dir():
            candidate_roots.append(hardware)
        if len(candidate_roots) > 12:
            break

    files: list[Path] = []
    seen: set[str] = set()
    for root in candidate_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_file():
                continue
            key = path.name.casefold()
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    if not files:
        return []

    assignments = assign_step_label_media_to_final_prompts(ir, files)
    by_name = {path.name: path for path in files}
    media_dir.mkdir(parents=True, exist_ok=True)
    applied: list[dict[str, Any]] = []

    def _copy_assignment(source: Path, target_name: str, meta: dict[str, Any]) -> None:
        destination = media_dir / Path(target_name).name
        if destination.exists() and destination.stat().st_size > 10_000:
            return
        shutil.copy2(source, destination)
        applied.append(
            {
                "input": source.name,
                "output": destination.name,
                **meta,
            }
        )

    for assignment in assignments:
        if assignment.get("status") != "mapped":
            continue
        source = by_name.get(str(assignment.get("input") or ""))
        if source is None or not source.is_file():
            continue
        suffix = source.suffix.lower()
        targets: list[str] = []
        if suffix in {".gif", ".mp4", ".webm", ".mov"} and assignment.get("video_output"):
            targets.append(str(assignment["video_output"]))
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"} and assignment.get("image_output"):
            targets.append(str(assignment["image_output"]))
        if suffix in {".png", ".jpg", ".jpeg"} and assignment.get("video_output") and not targets:
            targets.append(str(assignment["video_output"]))
        for target_name in targets:
            _copy_assignment(
                source,
                target_name,
                {
                    "prompt_number": assignment.get("prompt_number"),
                    "step_id": assignment.get("step_id"),
                    "mapping_basis": "final_visible_prompt_number",
                },
            )

    # Also map legacy stepN.* onto IR step_id slots (step16.gif -> step_016_*).
    slots_by_number: dict[int, dict[str, str]] = {}
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "prompt_user":
            continue
        step_id = str(step.get("id") or "")
        match = re.match(r"step_0*(\d+)$", step_id, flags=re.IGNORECASE)
        if not match:
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        placeholders = params.get("media_placeholders") if isinstance(params, dict) else None
        if not isinstance(placeholders, list) or not placeholders:
            continue
        slots = {
            str(item.get("kind") or "").lower(): str(item.get("slot") or "")
            for item in placeholders
            if isinstance(item, dict) and str(item.get("slot") or "")
        }
        slots_by_number[int(match.group(1))] = {
            "image": f"{slots['image']}.png" if slots.get("image") else "",
            "video": f"{slots['video']}.gif" if slots.get("video") else "",
            "step_id": step_id,
        }
    for path in files:
        match = re.match(r"^step(\d+)\.(?P<ext>[^.]+)$", path.name, flags=re.IGNORECASE)
        if not match:
            continue
        number = int(match.group(1))
        slots = slots_by_number.get(number)
        if not slots:
            continue
        ext = match.group("ext").lower()
        if ext in {"gif", "mp4", "webm", "mov"} and slots.get("video"):
            _copy_assignment(
                path,
                slots["video"],
                {"prompt_number": number, "step_id": slots["step_id"], "mapping_basis": "ir_step_id"},
            )
        if ext in {"png", "jpg", "jpeg", "bmp", "tif", "tiff"} and slots.get("image"):
            _copy_assignment(
                path,
                slots["image"],
                {"prompt_number": number, "step_id": slots["step_id"], "mapping_basis": "ir_step_id"},
            )
    return applied


def _archive_fs_mapping_directories(archive_path: Path) -> list[tuple[int, str]]:
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        return []
    with zipfile.ZipFile(archive_path, "r") as archive:
        mapping_name = next(
            (
                name
                for name in archive.namelist()
                if name.replace("\\", "/").casefold() == "fs/mapping.xml"
            ),
            None,
        )
        if not mapping_name:
            return []
        return parse_fs_mapping_directories(archive.read(mapping_name))


def _embed_generated_media_files(
    archive_path: Path,
    *,
    media_dir: Path | None,
    media_path_map: dict[str, Any] | None,
) -> dict[str, Any]:
    if media_dir is None or not media_path_map or not media_path_map.get("entries"):
        return {"skipped": True, "reason": "no_generated_media"}
    plan = plan_fs_embed(
        media_dir=media_dir,
        media_path_map=media_path_map,
        external_files_dir=None,
        external_entries=None,
        existing_directories=_archive_fs_mapping_directories(archive_path),
    )
    return embed_filesystem_in_archive(archive_path, plan)


def _normalize_windows_key(value: Any) -> str:
    return str(PureWindowsPath(str(value or "").strip().replace("/", "\\"))).rstrip("\\").casefold()


def _remove_generated_media_unresolved_paths(
    filesystem_packaging: dict[str, Any],
    generated_media_packaging: dict[str, Any],
) -> None:
    embedded = {
        _normalize_windows_key(item.get("target_absolute"))
        for item in generated_media_packaging.get("embedded_files") or []
        if isinstance(item, dict)
    }
    if not embedded:
        return
    unresolved = filesystem_packaging.get("unresolved_paths")
    if not isinstance(unresolved, list):
        return
    filesystem_packaging["unresolved_paths"] = [
        path for path in unresolved if _normalize_windows_key(path) not in embedded
    ]


def _write_project_import_archives(
    source_projects: list[Path],
    *,
    filesystem_source_archives: list[Path] | None = None,
    compiled_xscr: Path,
    destination_dir: Path,
    bundle_root: Path,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
    subroutine_artifacts: list[dict[str, Any]],
    media_dir: Path | None = None,
    media_path_map: dict[str, Any] | None = None,
    exports: list[ExportedArtifact] | None = None,
    copied_files: list[dict[str, str]] | None = None,
    target_script_folder: str | None = None,
) -> list[dict[str, Any]]:
    subroutine_artifacts = _dedupe_subroutine_artifacts(subroutine_artifacts)
    exports = exports if exports is not None else []
    copied_files = copied_files if copied_files is not None else []
    records: list[dict[str, Any]] = []
    readable_projects = [
        path
        for path in _dedupe_paths(source_projects)
        if path.exists() and path.suffix.lower() == ".zeia" and zipfile.is_zipfile(path)
    ]
    readable_filesystem_sources = [
        path
        for path in _dedupe_paths(filesystem_source_archives or readable_projects)
        if path.exists() and path.suffix.lower() == ".zeia" and zipfile.is_zipfile(path)
    ]
    for index, source_project in enumerate(readable_projects, start=1):
        filename = "generated_project.zeia" if index == 1 else f"generated_project_{index}.zeia"
        destination = destination_dir / filename
        ordered_filesystem_sources = [
            source_project,
            *[path for path in readable_filesystem_sources if path != source_project],
        ]
        record = _write_generated_project_archive(
            source_project,
            destination,
            compiled_xscr=compiled_xscr,
            bundle_root=bundle_root,
            source_manifest=source_manifest,
            source_xscr=source_xscr,
            source_scripts=source_scripts,
            subroutine_artifacts=subroutine_artifacts,
            media_dir=media_dir,
            media_path_map=media_path_map,
            target_script_folder=target_script_folder,
            filesystem_source_archives=ordered_filesystem_sources,
        )
        exports.append(ExportedArtifact(source_project, destination, "generated-project-archive"))
        copied_files.append(_file_record("generated-project-archive", source_project, destination, bundle_root=bundle_root))
        records.append(record)
    return records


def _force_full_zeia_copy() -> bool:
    """Opt-in full source ZEIA copy. Default packaging is script-scoped (Fluent or portable)."""
    return str(os.environ.get("TECAN_PACKAGE_FULL_ZEIA_COPY") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _write_generated_project_archive(
    source_project: Path,
    destination: Path,
    *,
    compiled_xscr: Path,
    bundle_root: Path,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
    subroutine_artifacts: list[dict[str, Any]],
    media_dir: Path | None = None,
    media_path_map: dict[str, Any] | None = None,
    target_script_folder: str | None = None,
    filesystem_source_archives: list[Path] | None = None,
) -> dict[str, Any]:
    if _force_full_zeia_copy():
        return _write_generated_project_archive_legacy_zip(
            source_project,
            destination,
            compiled_xscr=compiled_xscr,
            bundle_root=bundle_root,
            source_manifest=source_manifest,
            source_xscr=source_xscr,
            source_scripts=source_scripts,
            subroutine_artifacts=subroutine_artifacts,
            media_dir=media_dir,
            media_path_map=media_path_map,
            target_script_folder=target_script_folder,
            filesystem_source_archives=filesystem_source_archives,
        )
    return _write_generated_project_archive_script_scoped(
        source_project,
        destination,
        compiled_xscr=compiled_xscr,
        bundle_root=bundle_root,
        source_manifest=source_manifest,
        source_xscr=source_xscr,
        source_scripts=source_scripts,
        subroutine_artifacts=subroutine_artifacts,
        media_dir=media_dir,
        media_path_map=media_path_map,
        target_script_folder=target_script_folder,
        filesystem_source_archives=filesystem_source_archives,
    )


def _write_generated_project_archive_with_fluent_writer(
    source_project: Path,
    destination: Path,
    *,
    compiled_xscr: Path,
    bundle_root: Path,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
    subroutine_artifacts: list[dict[str, Any]],
    media_dir: Path | None = None,
    media_path_map: dict[str, Any] | None = None,
    target_script_folder: str | None = None,
    filesystem_source_archives: list[Path] | None = None,
) -> dict[str, Any]:
    """Backward-compatible alias for script-scoped packaging."""
    return _write_generated_project_archive_script_scoped(
        source_project,
        destination,
        compiled_xscr=compiled_xscr,
        bundle_root=bundle_root,
        source_manifest=source_manifest,
        source_xscr=source_xscr,
        source_scripts=source_scripts,
        subroutine_artifacts=subroutine_artifacts,
        media_dir=media_dir,
        media_path_map=media_path_map,
        target_script_folder=target_script_folder,
        filesystem_source_archives=filesystem_source_archives,
    )


def _write_generated_project_archive_script_scoped(
    source_project: Path,
    destination: Path,
    *,
    compiled_xscr: Path,
    bundle_root: Path,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
    subroutine_artifacts: list[dict[str, Any]],
    media_dir: Path | None = None,
    media_path_map: dict[str, Any] | None = None,
    target_script_folder: str | None = None,
    filesystem_source_archives: list[Path] | None = None,
) -> dict[str, Any]:
    subroutine_artifacts = _dedupe_subroutine_artifacts(subroutine_artifacts)
    use_fluent_writer = _fluent_archive_writer_available()
    packaging_method = "fluent_archive_writer" if use_fluent_writer else "portable_archive_writer"

    ensure_parent(destination)
    with zipfile.ZipFile(source_project, "r") as source_zip:
        archive_data = {info.filename: source_zip.read(info.filename) for info in source_zip.infolist()}
    dependency_archive_data = _dependency_archive_data(
        archive_data,
        filesystem_source_archives or [],
    )

    script_records = _archive_script_records(archive_data)
    if not script_records:
        raise PipelineError(f"source ZEIA has no script entries to derive metadata from: {source_project}")

    generated_name = _script_object_name_from_path(compiled_xscr) or compiled_xscr.stem
    main_record = _select_project_main_script(
        script_records,
        source_manifest=source_manifest,
        source_xscr=source_xscr,
        source_scripts=source_scripts,
        generated_name=generated_name,
    )
    if main_record is None:
        raise PipelineError(f"could not identify the main script metadata in source ZEIA: {source_project}")

    source_folder = str(main_record.get("folder") or "")
    generated_target_folder = _normalize_script_folder(target_script_folder)
    generated_payload = _prepare_project_script_payload(
        compiled_xscr,
        fallback_folder=source_folder,
        target_folder=generated_target_folder,
    )
    generated_payload, reference_findings = _strip_unavailable_optional_references(
        generated_payload, dependency_archive_data, source_label=generated_name
    )
    generated_payload = _postprocess_archive_writer_script_payload(generated_payload)

    local_scripts_inventory = build_scripts_inventory()
    generated_payload, subroutine_guid_rewrites = rewrite_script_reference_guids(
        generated_payload,
        local_scripts_inventory,
    )

    target_folder = (
        _script_folder_from_payload(generated_payload) or generated_target_folder or source_folder
    )
    main_collision = collision_preflight(
        local_scripts_inventory,
        generated_name,
        target_folder,
    )
    local_target_guid = find_local_script_guid(
        generated_name,
        target_folder,
        inventory=local_scripts_inventory,
    )
    replace_existing = (
        bool(main_record.get("guid"))
        and str(main_record.get("object_name") or "").casefold() == generated_name.casefold()
    )
    used_guids = _available_archive_guids(archive_data)
    # Prefer the installed FluentControl GUID for the same name+folder. Otherwise
    # FluentControl remaps the new GUID onto that object at import and then
    # rejects the pre-remap checksum (VX_APPFR_016_005 / InvalidChecksumException).
    if local_target_guid:
        script_guid = local_target_guid
        replace_existing = True
    elif replace_existing:
        script_guid = str(main_record["guid"])
    else:
        script_guid = _unique_project_guid(
            source_project,
            generated_name,
            compiled_xscr,
            used_guids,
        )

    recomputed = recompute_checksum_bytes(generated_payload)
    recomputed_entries: list[str] = []
    if recomputed is not None:
        generated_payload = recomputed
        recomputed_entries.append("generated_script")
    relative_path = f"UserSpecific\\{script_guid}.xscr"
    added_subroutines_for_audit: list[dict[str, str]] = []
    staged_subroutine_dependencies: list[dict[str, Any]] = []
    import_unsupported_dependencies: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="tecan_archive_writer_") as tmp:
        staging_root = Path(tmp)
        datastore_root = staging_root / "DataStore"
        staged_script = datastore_root / "UserSpecific" / f"{script_guid}.xscr"
        ensure_parent(staged_script)
        staged_script.write_bytes(generated_payload)
        metadata_path = staging_root / "archive_writer_metadata.json"
        dependency_records = _archive_writer_dependency_records(
            dependency_archive_data,
            root_guids=_script_reference_guids_from_payload(generated_payload),
            exclude_guids={script_guid, str(main_record.get("guid") or "")},
            skipped_import_unsupported=import_unsupported_dependencies,
        )
        metadata_records = [
            {
                "guid": script_guid,
                "relative_path": relative_path,
                "object_name": generated_name,
                "object_path": _script_folder_from_payload(generated_payload) or generated_target_folder or source_folder,
                "type": "Script",
                "version": 1,
                "type_version": _script_type_version_from_payload(generated_payload),
                "is_root": True,
                "was_manufacturer": False,
                "refs": _script_reference_guids_from_payload(generated_payload),
                "file_refs": _script_file_refs(generated_payload),
            }
        ]
        for record in dependency_records:
            source_entry = str(record.get("source_entry") or "")
            relative = str(record.get("relative_path") or "")
            if not source_entry or not relative:
                continue
            staged_dependency = datastore_root / Path(relative.replace("\\", "/"))
            ensure_parent(staged_dependency)
            staged_dependency.write_bytes(dependency_archive_data[source_entry])
            metadata_records.append(
                {key: value for key, value in record.items() if key != "source_entry"}
            )
        packaged_script_guids = {
            str(record.get("guid") or "").casefold()
            for record in dependency_records
            if str(record.get("guid") or "").strip()
        }
        excluded_subroutine_guids = {
            str(script_guid).casefold(),
            str(main_record.get("guid") or "").casefold(),
        }
        subroutine_reference_guids: list[str] = []
        for item in subroutine_artifacts:
            path = Path(str(item.get("path") or ""))
            if not path.exists():
                continue
            guid = str(item.get("guid") or _guid_from_archive_entry(str(item.get("entry") or ""))).strip()
            if not guid:
                continue
            guid_key = guid.casefold()
            if guid_key in packaged_script_guids or guid_key in excluded_subroutine_guids:
                continue
            object_name = str(item.get("object_name") or _script_object_name_from_path(path) or path.stem)
            folder = str(_script_folder_from_path(path) or _folder_from_subroutine_ref(item) or "")
            relative = _datastore_relative_entry(str(item.get("entry") or f"DataStore\\UserSpecific\\{guid}.xscr"))
            if not relative.replace("\\", "/").casefold().endswith(".xscr"):
                relative = f"UserSpecific\\{guid}.xscr"
            staged_dependency = datastore_root / Path(relative.replace("\\", "/"))
            ensure_parent(staged_dependency)
            payload = _prepare_project_script_payload(path, fallback_folder=folder)
            payload, sub_findings = _strip_unavailable_optional_references(
                payload,
                dependency_archive_data,
                source_label=object_name,
            )
            reference_findings.extend(sub_findings)
            payload = _postprocess_archive_writer_script_payload(payload)
            recomputed_subroutine = recompute_checksum_bytes(payload)
            archive_entry = f"DataStore\\{relative}"
            if recomputed_subroutine is not None:
                payload = recomputed_subroutine
                recomputed_entries.append(archive_entry)
            staged_dependency.write_bytes(payload)
            payload_refs = _script_reference_guids_from_payload(payload)
            subroutine_reference_guids.extend(payload_refs)
            metadata_records.append(
                {
                    "guid": guid,
                    "relative_path": relative,
                    "object_name": object_name,
                    "object_path": _script_folder_from_payload(payload) or folder,
                    "type": "Script",
                    "version": 1,
                    "type_version": _script_type_version_from_payload(payload),
                    "is_root": False,
                    "was_manufacturer": False,
                    "refs": payload_refs,
                    "file_refs": _script_file_refs(payload),
                }
            )
            dependency = {
                "guid": guid,
                "object_name": object_name,
                "type": "Script",
                "relative_path": relative,
            }
            staged_subroutine_dependencies.append(dependency)
            added_subroutines_for_audit.append(
                {
                    "object_name": object_name,
                    "entry": archive_entry,
                    "source": str(path),
                    "guid": guid,
                }
            )
            packaged_script_guids.add(guid_key)
        subroutine_dependency_records = _archive_writer_dependency_records(
            dependency_archive_data,
            root_guids=subroutine_reference_guids,
            exclude_guids={*packaged_script_guids, *excluded_subroutine_guids},
            skipped_import_unsupported=import_unsupported_dependencies,
        )
        for record in subroutine_dependency_records:
            guid_key = str(record.get("guid") or "").casefold()
            if not guid_key or guid_key in packaged_script_guids or guid_key in excluded_subroutine_guids:
                continue
            source_entry = str(record.get("source_entry") or "")
            relative = str(record.get("relative_path") or "")
            if not source_entry or not relative:
                continue
            staged_dependency = datastore_root / Path(relative.replace("\\", "/"))
            ensure_parent(staged_dependency)
            staged_dependency.write_bytes(dependency_archive_data[source_entry])
            metadata_records.append(
                {key: value for key, value in record.items() if key != "source_entry"}
            )
            dependency_records.append(record)
            packaged_script_guids.add(guid_key)
        metadata_path.write_text(json.dumps(metadata_records, indent=2), encoding="utf-8")
        if use_fluent_writer:
            writer_report = _run_fluent_archive_writer(
                script_path=staged_script,
                archive_path=destination,
                datastore_root=datastore_root,
                metadata_json=metadata_path,
            )
        else:
            writer_report = _run_portable_archive_writer(
                script_path=staged_script,
                archive_path=destination,
                datastore_root=datastore_root,
                metadata_json=metadata_path,
            )

    if not destination.exists() or not zipfile.is_zipfile(destination):
        raise PipelineError(
            f"{packaging_method} did not produce a readable ZEIA: {destination}"
        )

    final_entry = f"DataStore\\{relative_path}"
    archive_payload = generated_payload
    actual_final_entry = final_entry
    with zipfile.ZipFile(destination, "r") as zf:
        actual_final_entry = _find_archive_entry(zf.namelist(), final_entry) or final_entry
        if actual_final_entry in zf.namelist():
            archive_payload = zf.read(actual_final_entry)
    postprocessed_payload = _postprocess_archive_writer_script_payload(archive_payload)
    recomputed_after_writer = recompute_checksum_bytes(postprocessed_payload)
    if recomputed_after_writer is not None:
        postprocessed_payload = recomputed_after_writer
        recomputed_entries.append(actual_final_entry)
    if postprocessed_payload != archive_payload:
        _replace_zip_entry(destination, actual_final_entry, postprocessed_payload)
    archive_payload = postprocessed_payload
    # Archive writer can reintroduce baseline TouchTools FileReferences for the
    # same script GUID; drop orphans not used by prompt media fields.
    with tempfile.TemporaryDirectory(prefix="tecan_orphan_media_refs_") as orphan_tmp:
        orphan_xscr = Path(orphan_tmp) / "script.xscr"
        orphan_xscr.write_bytes(archive_payload)
        orphan_removed = strip_orphan_touchtools_media_file_references(orphan_xscr)
        if orphan_removed:
            cleaned = orphan_xscr.read_bytes()
            recomputed_clean = recompute_checksum_bytes(cleaned)
            if recomputed_clean is not None:
                cleaned = recomputed_clean
                recomputed_entries.append(actual_final_entry)
            _replace_zip_entry(destination, actual_final_entry, cleaned)
            archive_payload = cleaned
    filesystem_packaging = copy_referenced_filesystem_from_archives(
        filesystem_source_archives or [source_project],
        destination,
    )
    generated_media_packaging = _embed_generated_media_files(
        destination,
        media_dir=media_dir,
        media_path_map=media_path_map,
    )
    _remove_generated_media_unresolved_paths(filesystem_packaging, generated_media_packaging)

    checksum_audit = audit_archive_checksums({actual_final_entry: archive_payload}, mutated_entries={actual_final_entry})
    checksum_audit["recomputed_count"] = len(recomputed_entries)
    checksum_audit["recomputed_entries"] = sorted(set(recomputed_entries))

    owned_entries = {actual_final_entry, *(str(item.get("entry") or "") for item in added_subroutines_for_audit)}
    archive_audit = verify_generated_project_archive(
        destination,
        bundle_root=bundle_root,
        owned_entries=owned_entries,
    )
    subroutine_audit = verify_added_subroutine_metadata(
        destination,
        added_subroutines_for_audit,
        datastore_archive=True,
    )
    packaged_dependencies = [
        {
            "guid": str(item.get("guid") or ""),
            "object_name": str(item.get("object_name") or ""),
            "type": str(item.get("type") or ""),
            "relative_path": str(item.get("relative_path") or ""),
        }
        for item in [*dependency_records, *staged_subroutine_dependencies]
    ]

    warnings: list[str] = []
    for item in reference_findings:
        where = f" in script `{item['source_label']}`" if item.get("source_label") else ""
        type_label = item.get("type_id") or "model"
        warnings.append(
            f"MISSING DEPENDENCY: {type_label} `{item['object_name']}` ({item['guid']}) "
            f"is referenced{where} but is not in the source ZEIA base. No model was created; "
            "it must already exist in the target FluentControl system or library."
        )
    for item in import_unsupported_dependencies:
        warnings.append(
            f"TARGET DEPENDENCY NOT PACKAGED: {item['type']} `{item['object_name']}` ({item['guid']}) "
            "is referenced by a packaged script/subroutine, but FluentControl logs this source "
            "datastore type/key as unsupported and ignores it during import. It must already exist "
            "in the target FluentControl system or library."
        )
    for finding in archive_audit.get("blocking") or []:
        warnings.append(
            "BROKEN IMPORT ARTIFACT: "
            + _format_archive_finding(finding)
            + " The packaged generated_project.zeia will fail to load in FluentControl."
        )
    if subroutine_artifacts and not any(
        str(item.get("type") or "") == "Script" for item in packaged_dependencies
    ):
        warnings.append(
            "The generated ZEIA is script-only; source subroutine files are exported separately "
            "under direct-imports/scripts/subroutines and must already exist in the target context "
            "or be imported separately."
        )
    checksum_problem_count = (
        int(checksum_audit.get("blank_count") or 0)
        + int(checksum_audit.get("absent_count") or 0)
        + int(checksum_audit.get("invalid_count") or 0)
    )
    if checksum_problem_count:
        warnings.append(
            f"NOT IMPORT-CLEAN: {checksum_problem_count} edited entr(y/ies) ship without a valid "
            "<Checksum>. FluentControl validates checksums on load and will reject these entries."
        )
    if str(main_collision.get("status") or "") == "collision":
        collision_guids = ", ".join(str(item) for item in (main_collision.get("guids") or []))
        warnings.append(
            "LOCAL SCRIPT GUID COLLISION: "
            f"`{target_folder}\\{generated_name}` maps to multiple FluentControl GUIDs "
            f"({collision_guids}). Packaging kept a non-local GUID rather than guessing; "
            "import may remap and fail checksum (VX_APPFR_016_005). Pick one GUID or rename."
        )

    base_archive_guids = {
        guid
        for guid in (
            *(_guid_from_archive_entry(entry) for entry in dependency_archive_data),
            *(_guid_from_archive_entry(entry) for entry in archive_data),
        )
        if guid
    }
    target_prereq_report = report_missing_system_dependencies(
        generated_payload,
        base_archive_guids=base_archive_guids,
    )
    if int(target_prereq_report.get("missing_count") or 0):
        missing_names = ", ".join(
            f"{item.get('type_id')}:{item.get('object_name')}"
            for item in (target_prereq_report.get("missing") or [])[:8]
        )
        warnings.append(
            "TARGET_PREREQ missing on instrument/SystemSpecific (not packaged into ZEIA): "
            f"{missing_names}"
            + (
                " ..."
                if int(target_prereq_report.get("missing_count") or 0) > 8
                else ""
            )
        )

    return {
        "kind": "generated-project-archive",
        "packaging_method": packaging_method,
        "source_project": str(source_project),
        "relative_path": _bundle_relative_path(destination, bundle_root=bundle_root),
        "main_script": {
            "object_name": generated_name,
            "replaced_entry": str(main_record["entry"]) if replace_existing else "",
            "added_entry": final_entry,
            "source_object_name": str(main_record.get("object_name") or ""),
            "source_folder": source_folder,
            "target_folder": target_folder,
            "guid": script_guid,
            "guid_source": (
                "local_fluentcontrol_datastore"
                if local_target_guid
                else ("source_zeia_main_script" if replace_existing else "generated_unique")
            ),
            "local_target_guid": local_target_guid or "",
            "local_inventory_collision": str(main_collision.get("status") or "") == "collision",
            "local_inventory_guids": list(main_collision.get("guids") or []),
            "local_inventory_status": str(main_collision.get("status") or ""),
            "metadata_source": "source ZEIA script record plus generated XSCR references",
        },
        "subroutine_guid_rewrites": subroutine_guid_rewrites,
        "target_prereq_report": target_prereq_report,
        "subroutines_replaced": [],
        "subroutines_added": added_subroutines_for_audit,
        "dependencies_packaged": packaged_dependencies,
        "dependencies_not_packaged": import_unsupported_dependencies,
        "subroutine_dependencies": [
            item for item in packaged_dependencies if str(item.get("type") or "").casefold() == "script"
        ],
        "archive_metadata_entries_changed": ["DataStore\\nodedescription.xml", "meta\\content.xml"],
        "filesystem_packaging": filesystem_packaging,
        "generated_media_packaging": generated_media_packaging,
        "unresolved_references": reference_findings,
        "base_reuse": {
            "base_entry_count": len(archive_data),
            "script_entries_replaced": 1 if replace_existing else 0,
            "script_entries_added": 0 if replace_existing else 1,
            "models_created": 0,
            "note": (
                (
                    "Built with FluentControl's archive writer from generated script metadata derived "
                    "from the existing ZEIA context. "
                )
                if packaging_method == "fluent_archive_writer"
                else (
                    "Built with the portable script-scoped packager (Mac/Linux / no FluentControl "
                    "archive-writer assemblies). "
                )
            )
            + (
                "The archive contains the generated script plus referenced source-context datastore "
                "objects copied from the base ZEIA; no models, components, worktables, devices, or "
                "liquid classes were created."
            ),
        },
        "checksum_audit": checksum_audit,
        "archive_audit": archive_audit,
        "subroutine_audit": subroutine_audit,
        "writer_report": writer_report,
        "warnings": warnings,
        "zip_valid": zipfile.is_zipfile(destination),
        "checksum_note": _checksum_note(checksum_audit),
    }


def _write_generated_project_archive_legacy_zip(
    source_project: Path,
    destination: Path,
    *,
    compiled_xscr: Path,
    bundle_root: Path,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
    subroutine_artifacts: list[dict[str, Any]],
    media_dir: Path | None = None,
    media_path_map: dict[str, Any] | None = None,
    target_script_folder: str | None = None,
    filesystem_source_archives: list[Path] | None = None,
) -> dict[str, Any]:
    subroutine_artifacts = _dedupe_subroutine_artifacts(subroutine_artifacts)
    ensure_parent(destination)
    with zipfile.ZipFile(source_project, "r") as source_zip:
        infos = source_zip.infolist()
        archive_data = {info.filename: source_zip.read(info.filename) for info in infos}
    dependency_archive_data = _dependency_archive_data(
        archive_data,
        filesystem_source_archives or [],
    )

    script_records = _archive_script_records(archive_data)
    if not script_records:
        raise PipelineError(f"source ZEIA has no script entries to replace: {source_project}")

    generated_name = _script_object_name_from_path(compiled_xscr) or compiled_xscr.stem
    main_record = _select_project_main_script(
        script_records,
        source_manifest=source_manifest,
        source_xscr=source_xscr,
        source_scripts=source_scripts,
        generated_name=generated_name,
    )
    if main_record is None:
        raise PipelineError(f"could not identify the main script entry in source ZEIA: {source_project}")

    replacements: dict[str, bytes] = {}
    additions: dict[str, bytes] = {}
    warnings: list[str] = []
    changed_metadata_entries: list[str] = []
    replaced_subroutines: list[dict[str, str]] = []
    added_subroutines: list[dict[str, str]] = []

    datastore_archive = _archive_has_datastore_metadata(archive_data)
    source_folder = str(main_record.get("folder") or "")
    generated_target_folder = _normalize_script_folder(target_script_folder)
    generated_payload = _prepare_project_script_payload(
        compiled_xscr,
        fallback_folder=source_folder,
        target_folder=generated_target_folder,
    )
    generated_folder = _script_folder_from_payload(generated_payload) or generated_target_folder or source_folder
    generated_payload, reference_findings = _strip_unavailable_optional_references(
        generated_payload, dependency_archive_data, source_label=generated_name
    )
    with tempfile.TemporaryDirectory(prefix="tecan_orphan_media_refs_legacy_") as orphan_tmp:
        orphan_xscr = Path(orphan_tmp) / "script.xscr"
        orphan_xscr.write_bytes(generated_payload)
        if strip_orphan_touchtools_media_file_references(orphan_xscr):
            generated_payload = orphan_xscr.read_bytes()
    replacements[str(main_record["entry"])] = generated_payload

    used_guids = _available_archive_guids(archive_data)

    for item in subroutine_artifacts:
        path = Path(str(item.get("path") or ""))
        if not path.exists():
            continue
        object_name = str(item.get("object_name") or _script_object_name_from_path(path) or path.stem)
        folder = str(_script_folder_from_path(path) or _folder_from_subroutine_ref(item) or "")
        if item.get("ambiguous"):
            alternatives = ", ".join(
                str(alt.get("object_name") or alt.get("entry") or "?")
                for alt in item.get("alternatives") or []
            )
            warnings.append(
                f"Subroutine reference `{item.get('ref') or object_name}` matched more than one "
                f"source script; packaged `{object_name}` and skipped ambiguous alternative(s): "
                f"{alternatives or 'unknown'}. Verify the correct subroutine before import."
            )
        match = _find_archive_script_by_object_name(script_records, object_name)
        payload = _prepare_project_script_payload(path, fallback_folder=folder)
        payload, sub_findings = _strip_unavailable_optional_references(
            payload, dependency_archive_data, source_label=object_name
        )
        reference_findings.extend(sub_findings)
        if match is not None:
            replacements[str(match["entry"])] = payload
            replaced_subroutines.append(
                {
                    "object_name": object_name,
                    "entry": str(match["entry"]),
                    "source": str(path),
                }
            )
            continue

        guid = _unique_project_guid(source_project, object_name, path, used_guids)
        used_guids.add(guid.casefold())
        if datastore_archive:
            entry = f"DataStore\\UserSpecific\\{guid}.xscr"
        else:
            entry = f"Scripts/{_safe_label(object_name)}.xscr"
        entry = _unique_archive_entry(entry, archive_data, additions)
        additions[entry] = payload
        added_subroutines.append({"object_name": object_name, "entry": entry, "source": str(path), "guid": guid})

    for item in reference_findings:
        where = f" in script `{item['source_label']}`" if item.get("source_label") else ""
        if item.get("action") == "removed":
            warnings.append(
                f"Removed unresolved {item['type_id']} reference `{item['object_name']}` "
                f"({item['guid']}){where} because the script body uses no liquid classes. "
                "No model was created; confirm this liquid class is genuinely unused."
            )
        elif str(item.get("type_id") or "") == "LiquidClass":
            warnings.append(
                f"MISSING DEPENDENCY: LiquidClass `{item['object_name']}` ({item['guid']}) "
                f"is referenced{where} but is not in the source ZEIA base. No model was created; "
                "it must already exist in the target FluentControl liquid-class library or the "
                "method will fail at load/run."
            )
        else:
            type_label = item.get("type_id") or "model"
            warnings.append(
                f"MISSING DEPENDENCY: {type_label} `{item['object_name']}` ({item['guid']}) "
                f"is referenced{where} but is not in the source ZEIA base. No model was created to "
                "satisfy it; this model must already exist in the target FluentControl system or "
                "the method will fail to load."
            )

    if datastore_archive:
        if added_subroutines:
            node_name = _find_archive_entry(archive_data, "DataStore/nodedescription.xml")
            if node_name:
                current_node_bytes = replacements.get(node_name, archive_data[node_name])
                base_version = _next_nodedescription_version(_decode_xml_bytes(current_node_bytes))
                replacements[node_name] = _append_nodedescription_script_nodes(
                    current_node_bytes,
                    added_subroutines,
                    base_version=base_version,
                )
                changed_metadata_entries.append(node_name)
            else:
                warnings.append("Could not add datastore node descriptions for new subroutines; nodedescription.xml was not found.")

            content_name = _find_archive_entry(archive_data, "meta/content.xml")
            if content_name:
                replacements[content_name] = _append_content_datastore_entries(
                    replacements.get(content_name, archive_data[content_name]),
                    [item["entry"] for item in added_subroutines],
                )
                changed_metadata_entries.append(content_name)
            else:
                warnings.append("Could not add datastore content entries for new subroutines; meta/content.xml was not found.")

        node_name = _find_archive_entry(archive_data, "DataStore/nodedescription.xml")
        if node_name and main_record.get("guid"):
            updated = _update_nodedescription_script_identity(
                replacements.get(node_name, archive_data[node_name]),
                script_guid=str(main_record["guid"]),
                object_name=generated_name,
                folder=generated_folder,
            )
            if updated is not None:
                replacements[node_name] = updated
                changed_metadata_entries.append(node_name)

    mutated_entries = set(replacements) | set(additions)
    final_entries: dict[str, bytes] = {}
    recomputed_entries: list[str] = []
    for entry in mutated_entries:
        data = additions.get(entry) if entry in additions else replacements.get(entry)
        if data is None:
            continue
        rewritten = recompute_checksum_bytes(data)
        if rewritten is not None and rewritten != data:
            if entry in additions:
                additions[entry] = rewritten
            else:
                replacements[entry] = rewritten
            data = rewritten
            recomputed_entries.append(entry)
        final_entries[entry] = data

    with zipfile.ZipFile(destination, "w") as out_zip:
        written = set()
        for info in infos:
            data = replacements.get(info.filename, archive_data[info.filename])
            out_zip.writestr(info, data)
            written.add(info.filename)
        for entry, data in additions.items():
            if entry not in written:
                out_zip.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED)
    _restore_windows_datastore_zip_names(destination)
    filesystem_packaging = copy_referenced_filesystem_from_archives(
        filesystem_source_archives or [source_project],
        destination,
    )
    generated_media_packaging = _embed_generated_media_files(
        destination,
        media_dir=media_dir,
        media_path_map=media_path_map,
    )
    _remove_generated_media_unresolved_paths(filesystem_packaging, generated_media_packaging)

    checksum_audit = audit_archive_checksums(final_entries, mutated_entries=mutated_entries)
    checksum_audit["recomputed_count"] = len(recomputed_entries)
    checksum_audit["recomputed_entries"] = sorted(recomputed_entries)

    owned_entries = {
        entry
        for entry in mutated_entries
        if str(entry).replace("\\", "/").lower().endswith(".xscr")
    }
    owned_entries.update(str(item.get("entry") or "") for item in added_subroutines)
    owned_entries.update(str(item.get("entry") or "") for item in replaced_subroutines)
    archive_audit = verify_generated_project_archive(
        destination,
        bundle_root=bundle_root,
        owned_entries=owned_entries,
    )
    subroutine_audit = verify_added_subroutine_metadata(
        destination,
        added_subroutines,
        datastore_archive=datastore_archive,
    )

    project_record = {
        "kind": "generated-project-archive",
        "packaging_method": "python_zip_fallback",
        "source_project": str(source_project),
        "relative_path": _bundle_relative_path(destination, bundle_root=bundle_root),
        "main_script": {
            "object_name": generated_name,
            "replaced_entry": str(main_record["entry"]),
            "source_object_name": str(main_record.get("object_name") or ""),
            "source_folder": source_folder,
            "target_folder": generated_folder,
        },
        "subroutines_replaced": replaced_subroutines,
        "subroutines_added": added_subroutines,
        "subroutine_dependencies": subroutine_dependency_records_from_artifacts(subroutine_artifacts),
        "archive_metadata_entries_changed": sorted(set(changed_metadata_entries)),
        "filesystem_packaging": filesystem_packaging,
        "generated_media_packaging": generated_media_packaging,
        "unresolved_references": reference_findings,
        "base_reuse": {
            "base_entry_count": len(infos),
            "script_entries_replaced": len(replacements) - len(set(changed_metadata_entries)),
            "script_entries_added": len(additions),
            "models_created": 0,
            "note": (
                "Built from the exact source ZEIA base; all original entries are preserved "
                "and only script entries (and their datastore metadata) were added or replaced. "
                "No models, components, or liquid classes were created."
            ),
        },
        "checksum_audit": checksum_audit,
        "archive_audit": archive_audit,
        "subroutine_audit": subroutine_audit,
        "warnings": warnings,
        "zip_valid": zipfile.is_zipfile(destination),
        "checksum_note": _checksum_note(checksum_audit),
    }
    for finding in archive_audit.get("blocking") or []:
        warnings.append(
            "BROKEN IMPORT ARTIFACT: "
            + _format_archive_finding(finding)
            + " The packaged generated_project.zeia will fail to load in FluentControl."
        )
    for finding in subroutine_audit.get("blocking") or []:
        warnings.append("ADDED SUBROUTINE METADATA DEFECT: " + _format_subroutine_finding(finding))
    if subroutine_audit.get("added"):
        warnings.append(
            f"{len(subroutine_audit['added'])} subroutine(s) were ADDED to the base ZEIA (not "
            "found in the source base). Adding a brand-new datastore object is inherently riskier "
            "than replacing an existing one. For the safest import, build the subroutine into the "
            "base ZEIA in FluentControl first, then re-run so the pipeline replaces the existing "
            "entry (reusing its GUID/metadata) instead of synthesizing new metadata."
        )
    if checksum_audit.get("blank_count"):
        warnings.append(
            f"NOT IMPORT-CLEAN: {checksum_audit['blank_count']} edited entr(y/ies) ship with a "
            "blank <Checksum> because the FluentControl checksum bridge (fluentcontrol_core) is "
            "not available here. FluentControl will reject or prompt to recalculate these on load. "
            "Recompute checksums on a machine with FluentControl before import, or accept the "
            "in-app recalculation prompt."
        )
    return project_record


_INHERITED_BASE_EXPORT_FINDING_KINDS = frozenset(
    {
        "invalid_expression",
        "invalid_checksum",
        "script_node_identity_mismatch",
        "script_node_identity_missing",
        "filesystem_missing_fs_payload",
        "filesystem_unmapped_file_reference",
    }
)


def _owned_archive_entry_keys(entries: Iterable[str] | None) -> set[str] | None:
    """Normalize packaging-owned archive entry paths; ``None`` means no ownership filter."""
    if entries is None:
        return None
    return {_normalize_archive_entry(str(entry)) for entry in entries if str(entry or "").strip()}


def _finding_targets_owned_entry(finding: Mapping[str, Any], owned: set[str] | None) -> bool:
    if owned is None:
        return True
    entry = finding.get("entry")
    if entry is None or not str(entry).strip():
        # Structural findings without a script entry stay owned/blocking.
        return True
    return _normalize_archive_entry(str(entry)) in owned


def _is_inherited_base_export_finding_kind(kind: str) -> bool:
    value = str(kind or "")
    return value in _INHERITED_BASE_EXPORT_FINDING_KINDS or value.startswith("filesystem_")


def _demote_inherited_base_export_findings(
    blocking: list[dict[str, Any]],
    needs_review: list[dict[str, Any]],
    *,
    owned_entries: set[str] | None,
) -> None:
    """Legacy full-copy packaging preserves unrelated base scripts.

    Expression / identity / filesystem defects on those carry-over scripts are
    inherited from the source ZEIA, not introduced by this generation. When the
    packager reports which entries it owns (generated/replaced/added scripts),
    demote non-owned inherited findings to needs-review so Gate 24 stays focused
    on packaging integrity for the new method.
    """
    if owned_entries is None:
        return
    kept: list[dict[str, Any]] = []
    for item in blocking:
        kind = str(item.get("kind") or "")
        if _is_inherited_base_export_finding_kind(kind) and not _finding_targets_owned_entry(
            item, owned_entries
        ):
            detail = str(item.get("detail") or "").rstrip()
            suffix = "Inherited from the base ZEIA export; not owned by this generation."
            needs_review.append(
                {
                    **item,
                    "inherited_from_base_export": True,
                    "detail": f"{detail} {suffix}".strip() if detail else suffix,
                }
            )
            continue
        kept.append(item)
    blocking[:] = kept


def verify_generated_project_archive(
    archive_path: Path,
    *,
    bundle_root: Path | None = None,
    owned_entries: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Open a packaged ``generated_project.zeia`` and verify it is importable.

    This re-reads the *written* archive (not the in-memory packaging plan) and runs
    the same reference-resolution logic packaging uses, plus zip-integrity and
    datastore-metadata consistency checks. Findings are split into:

    * ``blocking`` — FluentControl will fail to load the file (corrupt zip, a used
      ``<Reference>`` GUID that is absent from the archive, or a datastore entry
      declared in ``meta/content.xml`` with no matching file), and
    * ``needs_review`` — the file loads but a dependency must already exist in the
      target system (dead/unused liquid-class reference, a datastore script not
      registered in ``content.xml``, or a blank checksum).

    When ``owned_entries`` is provided (generated/replaced/added script paths),
    inherited expression/identity/filesystem defects on other base-export scripts
    are demoted to needs-review. Omit ``owned_entries`` to keep the strict audit
    used by unit tests and standalone verify.

    Never raises; structural problems are returned as findings.
    """
    owned_entry_keys = _owned_archive_entry_keys(owned_entries)
    audit: dict[str, Any] = {
        "archive": _bundle_relative_path(archive_path, bundle_root=bundle_root or READY_TO_IMPORT_DIR),
        "zip_ok": False,
        "entry_count": 0,
        "blocking": [],
        "needs_review": [],
        "owned_entries": sorted(owned_entry_keys) if owned_entry_keys is not None else None,
        "expression_inventory": {
            "valid": True,
            "script_count": 0,
            "record_count": 0,
            "failure_count": 0,
            "failures": [],
        },
    }
    blocking: list[dict[str, Any]] = audit["blocking"]
    needs_review: list[dict[str, Any]] = audit["needs_review"]

    if not archive_path.exists():
        blocking.append({"kind": "missing_archive", "detail": "the archive was not written"})
        return audit
    if not zipfile.is_zipfile(archive_path):
        blocking.append({"kind": "not_a_zip", "detail": "the file is not a valid ZIP archive"})
        return audit

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            corrupt = zf.testzip()
            if corrupt is not None:
                blocking.append(
                    {"kind": "corrupt_entry", "entry": corrupt, "detail": "failed CRC integrity check"}
                )
            archive_data = {info.filename: zf.read(info.filename) for info in zf.infolist()}
    except (zipfile.BadZipFile, OSError) as exc:
        blocking.append({"kind": "unreadable_zip", "detail": str(exc)})
        return audit

    audit["zip_ok"] = not any(item["kind"] in {"corrupt_entry"} for item in blocking)
    audit["entry_count"] = len(archive_data)
    for finding in audit_archive_filesystem(archive_data):
        blocking.append(
            {
                **finding,
                "kind": f"filesystem_{finding.get('kind') or 'invalid'}",
            }
        )

    available_guids = _available_archive_guids(archive_data)
    expression_script_inventories: list[dict[str, Any]] = []
    source_preserved_context = source_preserved_expression_context_from_bundle(
        bundle_root or archive_path.parent
    )
    audit["expression_provenance"] = source_preserved_context["verification"]
    source_preserved_allowlist = source_preserved_context["allowlist"]

    seen_refs: set[tuple[str, str]] = set()
    for entry, data in archive_data.items():
        if not entry.replace("\\", "/").lower().endswith(".xscr"):
            continue
        text = _decode_xml_bytes(data)
        expression_inventory = expression_inventory_from_xscr_text(
            text,
            script=_first_xml_text_from_text(text, "ObjectName") or Path(entry).stem,
            entry=entry,
            source_preserved_allowlist=source_preserved_allowlist,
        )
        expression_script_inventories.append(expression_inventory)
        for failure in expression_inventory.get("failures") or []:
            blocking.append({
                "kind": "invalid_expression",
                "entry": entry,
                "script": failure.get("script"),
                "line": failure.get("line"),
                "command": failure.get("command"),
                "field": failure.get("field"),
                "variable": failure.get("variable"),
                "raw_expression": failure.get("raw_expression"),
                "reason": failure.get("reason"),
                "offset": failure.get("offset"),
                "semantic_issues": failure.get("semantic_issues"),
                "detail": "FluentControl expression failed typed expression validation.",
            })
        liquid_class_usage = bool(re.search(r"<LiquidClassName(?:BySelection)?>(?!\s*</)", text))
        checksum_state = entry_checksum_state(data)
        if checksum_state == "invalid":
            blocking.append(
                {
                    "kind": "invalid_checksum",
                    "entry": entry,
                    "detail": (
                        "script contains a malformed, duplicate, or stale <Checksum>; "
                        "FluentControl will reject it during import"
                    ),
                }
            )
        elif checksum_state == "blank":
            needs_review.append(
                {
                    "kind": "blank_checksum",
                    "entry": entry,
                    "detail": "edited entry ships with a blank <Checksum> (see Gate 23)",
                }
            )
        for block in re.findall(r"<Reference>.*?</Reference>", text, flags=re.DOTALL):
            guid = _first_xml_text_from_text(block, "Guid")
            if not guid or guid.casefold() in available_guids:
                continue
            key = (entry, guid.casefold())
            if key in seen_refs:
                continue
            seen_refs.add(key)
            type_id = _first_xml_text_from_text(block, "TypeId") or "model"
            object_name = _first_xml_text_from_text(block, "ObjectName")
            used = True
            if type_id == "LiquidClass":
                used = liquid_class_usage and (
                    not object_name
                    or bool(
                        re.search(
                            r"<LiquidClassName[^>]*>\s*" + re.escape(object_name) + r"\s*</LiquidClassName",
                            text,
                            re.IGNORECASE,
                        )
                    )
                )
            # A dangling <Reference> GUID does not corrupt the import artifact itself;
            # FluentControl resolves it against the target system's global model/liquid
            # library at load/run. The file still imports, so this is needs-review (the
            # dependency must exist in the target), not a blocking structural defect.
            needs_review.append(
                {
                    "kind": "unresolved_reference",
                    "entry": entry,
                    "type_id": type_id,
                    "guid": guid,
                    "object_name": object_name,
                    "used_in_script": used,
                }
            )

    _audit_datastore_metadata(archive_data, blocking=blocking, needs_review=needs_review)
    expression_failures = [
        failure
        for inventory in expression_script_inventories
        for failure in (inventory.get("failures") or [])
    ]
    audit["expression_inventory"] = {
        "valid": not expression_failures,
        "script_count": len(expression_script_inventories),
        "record_count": sum(int(item.get("record_count") or 0) for item in expression_script_inventories),
        "failure_count": len(expression_failures),
        "scripts": expression_script_inventories,
        "failures": expression_failures[:50],
    }
    _demote_inherited_base_export_findings(
        blocking,
        needs_review,
        owned_entries=owned_entry_keys,
    )

    return audit


def _audit_datastore_metadata(
    archive_data: dict[str, bytes],
    *,
    blocking: list[dict[str, Any]],
    needs_review: list[dict[str, Any]],
) -> None:
    if not _archive_has_datastore_metadata(archive_data):
        return
    content_name = _find_archive_entry(archive_data, "meta/content.xml")
    declared: set[str] = set()
    if content_name:
        content_text = _decode_xml_bytes(archive_data[content_name])
        block = re.search(r"<DatastoreEntries>(.*?)</DatastoreEntries>", content_text, flags=re.DOTALL)
        scope = block.group(1) if block else ""
        for raw in re.findall(r"<Entry>(.*?)</Entry>", scope):
            value = raw.strip()
            if not value:
                continue
            declared.add(_normalize_archive_entry(_datastore_relative_entry(value)))
            if (
                _find_archive_entry(archive_data, f"DataStore/{value}") is None
                and _find_archive_entry(archive_data, value) is None
            ):
                blocking.append(
                    {
                        "kind": "metadata_entry_missing",
                        "entry": value,
                        "detail": "declared in meta/content.xml but no matching archive file exists",
                    }
                )
    for entry in archive_data:
        normalized = entry.replace("\\", "/").lower()
        if not normalized.endswith(".xscr") or "datastore/" not in normalized:
            continue
        relative = _normalize_archive_entry(_datastore_relative_entry(entry))
        if relative not in declared:
            needs_review.append(
                {
                    "kind": "unregistered_datastore_script",
                    "entry": entry,
                    "detail": "datastore script is not registered in meta/content.xml",
                }
            )
    _audit_script_node_identity(archive_data, blocking=blocking)


def _audit_script_node_identity(
    archive_data: dict[str, bytes],
    *,
    blocking: list[dict[str, Any]],
) -> None:
    """Block metadata that would make FluentControl remap a checksummed script."""
    nodes = _archive_nodedescription_records(archive_data)
    if not nodes:
        return
    for script in _archive_script_records(archive_data):
        guid = str(script.get("guid") or "")
        if not guid:
            continue
        node = nodes.get(guid.casefold())
        if node is None:
            blocking.append(
                {
                    "kind": "script_node_identity_missing",
                    "entry": script["entry"],
                    "guid": guid,
                    "detail": (
                        "script has no matching nodedescription.xml record; "
                        "FluentControl may remap it before checksum validation"
                    ),
                }
            )
            continue
        for field, payload_key, node_key in (
            ("name", "object_name", "object_name"),
            ("folder", "folder", "object_path"),
        ):
            payload_value = str(script.get(payload_key) or "")
            node_value = str(node.get(node_key) or "")
            if payload_value == node_value:
                continue
            blocking.append(
                {
                    "kind": "script_node_identity_mismatch",
                    "entry": script["entry"],
                    "guid": guid,
                    "field": field,
                    "payload_value": payload_value,
                    "node_value": node_value,
                    "detail": (
                        f"script payload {field} {payload_value!r} does not match "
                        f"nodedescription.xml {field} {node_value!r}; FluentControl "
                        "will remap the object and then reject its checksum"
                    ),
                }
            )


def _format_archive_finding(finding: dict[str, Any]) -> str:
    kind = finding.get("kind")
    entry = finding.get("entry")
    if kind == "unresolved_reference":
        type_id = finding.get("type_id") or "model"
        name = finding.get("object_name") or "?"
        return (
            f"`{type_id}` reference `{name}` ({finding.get('guid')}) in `{entry}` "
            "does not resolve to any object in the archive."
        )
    if kind == "metadata_entry_missing":
        return f"datastore entry `{entry}` is declared in content.xml but missing from the archive."
    if kind == "corrupt_entry":
        return f"archive entry `{entry}` failed its CRC integrity check."
    if kind == "invalid_expression":
        variable = finding.get("variable") or "?"
        reason = finding.get("reason") or "parse_error"
        raw = finding.get("raw_expression") or ""
        return f"`{entry}` SetVariable `{variable}` has invalid expression {raw!r}: {reason}."
    detail = finding.get("detail") or kind or "unknown problem"
    return f"{detail}." if entry is None else f"`{entry}`: {detail}."


_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def verify_added_subroutine_metadata(
    archive_path: Path,
    added_subroutines: list[dict[str, str]],
    *,
    datastore_archive: bool,
) -> dict[str, Any]:
    """Audit the datastore metadata quality of newly-ADDED subroutines.

    Replaced subroutines reuse an existing GUID/entry and are inherently safe, so
    they are not audited here. Newly-added subroutines get synthesized metadata
    (a fresh GUID, an incremented ``<V>``, ``<FileRef>`` lines) and are therefore
    the risky path. This re-opens the *written* archive and confirms, per added
    subroutine:

    * the added ``.xscr`` entry is actually present (else **blocking**),
    * its GUID is a well-formed UUID (else **blocking** — datastore identity is broken),
    * for a datastore archive, the GUID has a matching ``<Id>`` node in
      ``nodedescription.xml`` carrying a positive ``<V>`` (a missing node is
      **blocking**; a missing/zero version is **needs-review**).

    Findings complement Gate 24 (which checks zip/content.xml consistency); this
    focuses on added-subroutine node identity and version quality. Never raises.
    """
    audit: dict[str, Any] = {
        "added": [],
        "replaced_audited": False,
        "blocking": [],
        "needs_review": [],
    }
    if not added_subroutines:
        return audit
    blocking: list[dict[str, Any]] = audit["blocking"]
    needs_review: list[dict[str, Any]] = audit["needs_review"]

    node_text = ""
    archive_entries: set[str] = set()
    normalized_archive_entries: set[str] = set()
    if archive_path.exists() and zipfile.is_zipfile(archive_path):
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                archive_entries = set(zf.namelist())
                normalized_archive_entries = {_normalize_archive_entry(name) for name in archive_entries}
                if datastore_archive:
                    node_name = _find_archive_entry(
                        {name: b"" for name in archive_entries}, "DataStore/nodedescription.xml"
                    )
                    if node_name:
                        node_text = _decode_xml_bytes(zf.read(node_name))
        except (zipfile.BadZipFile, OSError) as exc:
            blocking.append({"kind": "unreadable_archive", "detail": str(exc)})
            return audit

    for item in added_subroutines:
        entry = str(item.get("entry") or "")
        guid = str(item.get("guid") or "")
        object_name = str(item.get("object_name") or "")
        record = {"object_name": object_name, "entry": entry, "guid": guid}
        if entry and _normalize_archive_entry(entry) not in normalized_archive_entries:
            blocking.append({"kind": "added_entry_missing", **record})
        if not guid or not _GUID_RE.match(guid):
            blocking.append({"kind": "malformed_guid", **record})
        elif datastore_archive and node_text:
            block_match = re.search(
                r"<S\b[^>]*>(?:(?!</S>).)*?<Id>" + re.escape(guid) + r"</Id>.*?</S>",
                node_text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            if block_match is None:
                blocking.append({"kind": "node_missing", **record})
            else:
                version_match = re.search(r"<V>(\d+)</V>", block_match.group(0))
                version = int(version_match.group(1)) if version_match else 0
                record["version"] = version
                if version <= 0:
                    needs_review.append({"kind": "node_version_missing", **record})
        audit["added"].append(record)
    return audit


def _format_subroutine_finding(finding: dict[str, Any]) -> str:
    kind = finding.get("kind")
    name = finding.get("object_name") or "?"
    entry = finding.get("entry") or "?"
    if kind == "added_entry_missing":
        return f"added subroutine `{name}` is missing its archive entry `{entry}`."
    if kind == "malformed_guid":
        return f"added subroutine `{name}` (`{entry}`) has a malformed GUID `{finding.get('guid')}`."
    if kind == "node_missing":
        return (
            f"added subroutine `{name}` (`{entry}`) has no matching node in nodedescription.xml; "
            "FluentControl will not see the script."
        )
    if kind == "node_version_missing":
        return f"added subroutine `{name}` (`{entry}`) has a missing/zero datastore version (<V>)."
    detail = finding.get("detail") or kind or "unknown problem"
    return f"added subroutine `{name}` (`{entry}`): {detail}."


def _merge_project_audits(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse per-archive audits into context payloads for the ready gates."""
    archive_blocking: list[dict[str, Any]] = []
    archive_needs_review: list[dict[str, Any]] = []
    archive_summaries: list[dict[str, Any]] = []
    zip_ok = True
    blank_entries: list[str] = []
    absent_entries: list[str] = []
    invalid_entries: list[str] = []
    bridge_available = True
    sub_added: list[dict[str, Any]] = []
    sub_blocking: list[dict[str, Any]] = []
    sub_needs_review: list[dict[str, Any]] = []
    sub_dependencies: list[dict[str, Any]] = []
    replaced_count = 0
    for record in records:
        archive_audit = record.get("archive_audit") or {}
        name = archive_audit.get("archive") or record.get("relative_path") or "generated_project.zeia"
        for item in archive_audit.get("blocking") or []:
            archive_blocking.append({**item, "archive": name})
        for item in archive_audit.get("needs_review") or []:
            archive_needs_review.append({**item, "archive": name})
        if not archive_audit.get("zip_ok", True):
            zip_ok = False
        subroutine_audit = record.get("subroutine_audit") or {}
        for item in subroutine_audit.get("added") or []:
            sub_added.append({**item, "archive": name})
        for item in subroutine_audit.get("blocking") or []:
            sub_blocking.append({**item, "archive": name})
        for item in subroutine_audit.get("needs_review") or []:
            sub_needs_review.append({**item, "archive": name})
        dependency_records = record.get("subroutine_dependencies")
        if dependency_records is None:
            dependency_records = [
                item
                for item in record.get("dependencies_packaged") or []
                if isinstance(item, dict) and str(item.get("type") or "").casefold() == "script"
            ]
        for item in dependency_records or []:
            if isinstance(item, dict):
                sub_dependencies.append({**item, "archive": name})
        replaced_count += len(record.get("subroutines_replaced") or [])
        archive_summaries.append(
            {
                "archive": name,
                "zip_ok": archive_audit.get("zip_ok"),
                "entry_count": archive_audit.get("entry_count"),
                "blocking_count": len(archive_audit.get("blocking") or []),
                "needs_review_count": len(archive_audit.get("needs_review") or []),
            }
        )
        checksum_audit = record.get("checksum_audit") or {}
        blank_entries.extend(checksum_audit.get("blank_entries") or [])
        absent_entries.extend(checksum_audit.get("absent_entries") or [])
        invalid_entries.extend(checksum_audit.get("invalid_entries") or [])
        if not checksum_audit.get("bridge_available", False):
            bridge_available = False
    return {
        "project_archive_audit": {
            "zip_ok": zip_ok,
            "archives": archive_summaries,
            "blocking": archive_blocking,
            "needs_review": archive_needs_review,
        },
        "project_checksum_audit": {
            "blank_entries": sorted(set(blank_entries)),
            "absent_entries": sorted(set(absent_entries)),
            "invalid_entries": sorted(set(invalid_entries)),
            "bridge_available": bridge_available,
        },
        "project_subroutine_audit": {
            "added": sub_added,
            "replaced_count": replaced_count,
            "dependencies": sub_dependencies,
            "blocking": sub_blocking,
            "needs_review": sub_needs_review,
        },
    }


def _write_project_import_report_artifacts(
    records: list[dict[str, Any]],
    *,
    reports_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> None:
    json_dest = reports_dir / "project_import_report.json"
    ensure_parent(json_dest)
    json_dest.write_text(json.dumps({"project_imports": records}, indent=2, sort_keys=True), encoding="utf-8")
    exports.append(ExportedArtifact(json_dest, json_dest, "project-import-report-json"))
    copied_files.append(_file_record("project-import-report-json", json_dest, json_dest, bundle_root=bundle_root))

    md_dest = reports_dir / "project_import_report.md"
    md_dest.write_text(_render_project_import_report(records), encoding="utf-8")
    exports.append(ExportedArtifact(md_dest, md_dest, "project-import-report"))
    copied_files.append(_file_record("project-import-report", md_dest, md_dest, bundle_root=bundle_root))


def _checksum_note(audit: dict[str, Any]) -> str:
    blank = int(audit.get("blank_count") or 0)
    absent = int(audit.get("absent_count") or 0)
    invalid = int(audit.get("invalid_count") or 0)
    recomputed = int(audit.get("recomputed_count") or 0)
    if invalid:
        return (
            f"{invalid} edited entr(y/ies) have malformed, duplicate, or stale <Checksum> "
            "elements. FluentControl will reject these entries. This bundle is NOT import-clean."
        )
    if absent:
        return (
            f"{absent} edited entr(y/ies) are missing <Checksum>. FluentControl validates "
            "<Checksum> on load and will reject these entries. This bundle is NOT import-clean "
            "until checksums are recomputed on a machine with FluentControl."
        )
    if blank:
        return (
            f"{blank} edited entr(y/ies) have a blank <Checksum>. The FluentControl checksum "
            "bridge (fluentcontrol_core) is not available in this environment, so these could "
            "not be recomputed. FluentControl validates <Checksum> on load and will reject or "
            "prompt to recalculate. This bundle is NOT import-clean until checksums are "
            "recomputed on a machine with FluentControl."
        )
    if recomputed:
        return (
            f"All {recomputed} edited entr(y/ies) were re-checksummed with the FluentControl "
            "bridge; preserved base entries keep their original valid checksums. Import-clean."
        )
    return (
        "No edited entries required a checksum; all shipped entries retain their original "
        "source checksums. Import-clean."
    )


def _render_project_import_report(records: list[dict[str, Any]]) -> str:
    lines = ["# Project Import Archive", ""]
    for record in records:
        main = record.get("main_script") or {}
        lines.extend(
            [
                f"- Generated ZEIA: `{record.get('relative_path')}`",
                f"- Source ZEIA: `{record.get('source_project')}`",
                f"- Main script entry: `{main.get('replaced_entry')}`",
                f"- Main script object: `{main.get('object_name')}`",
                f"- Zip readable: `{record.get('zip_valid')}`",
                "- Readiness boundary: this report covers generated ZEIA import health only; "
                "Script Editor load-clean requires the optional Gate 27 FluentControl import/load diagnostic or a manual open/load check, and hardware-run-ready requires operator validation on the target instrument.",
            ]
        )
        base = record.get("base_reuse") or {}
        if base:
            lines.append(
                f"- Base reuse: preserved `{base.get('base_entry_count')}` source entries, "
                f"replaced `{base.get('script_entries_replaced')}` script(s), "
                f"added `{base.get('script_entries_added')}` script(s), "
                f"created `{base.get('models_created')}` model(s)."
            )
            if base.get("note"):
                lines.append(f"  - {base['note']}")
        if record.get("subroutines_replaced"):
            lines.append("- Replaced subroutines:")
            for item in record["subroutines_replaced"]:
                lines.append(f"  - `{item.get('object_name')}` -> `{item.get('entry')}`")
        if record.get("subroutines_added"):
            lines.append("- Added subroutines:")
            for item in record["subroutines_added"]:
                lines.append(f"  - `{item.get('object_name')}` -> `{item.get('entry')}`")
        if record.get("subroutine_dependencies"):
            lines.append("- Subroutine dependencies:")
            for item in record["subroutine_dependencies"]:
                pieces = [
                    f"`{item.get('ref') or item.get('object_name')}`",
                    f"object `{item.get('object_name')}`",
                ]
                if item.get("folder"):
                    pieces.append(f"folder `{item.get('folder')}`")
                if item.get("guid"):
                    pieces.append(f"GUID `{item.get('guid')}`")
                if item.get("entry"):
                    pieces.append(f"entry `{item.get('entry')}`")
                if item.get("version"):
                    pieces.append(f"version `{item.get('version')}`")
                lines.append("  - " + "; ".join(pieces))
        if record.get("dependencies_not_packaged"):
            lines.append("- Target dependencies not packaged:")
            lines.append(
                "  - These references stay in the script, but their source datastore objects are not "
                "shipped because FluentControl logs the corresponding DataStoreKey as unsupported "
                "and ignores it during import."
            )
            for item in record["dependencies_not_packaged"]:
                key = f", DataStoreKey `{item.get('datastore_key')}`" if item.get("datastore_key") else ""
                lines.append(
                    f"  - `{item.get('object_name')}` ({item.get('type')} {item.get('guid')}{key}); "
                    "must already exist in the target FluentControl system/library"
                )
        unresolved = record.get("unresolved_references") or []
        if unresolved:
            missing = [item for item in unresolved if item.get("action") != "removed"]
            removed = [item for item in unresolved if item.get("action") == "removed"]
            if missing:
                lines.append(
                    "- Missing model dependencies (referenced but not in the source ZEIA base; "
                    "no model was created):"
                )
                for item in missing:
                    used = "used by script" if item.get("used_in_script") else "usage unconfirmed"
                    where = f", script `{item['source_label']}`" if item.get("source_label") else ""
                    lines.append(
                        f"  - `{item.get('object_name')}` ({item.get('type_id')} "
                        f"{item.get('guid')}{where}) - {used}; must already exist in the target "
                        "FluentControl system or the method will fail to load/run"
                    )
            if removed:
                lines.append("- Removed unresolved references (unused in script body):")
                for item in removed:
                    where = f", script `{item['source_label']}`" if item.get("source_label") else ""
                    lines.append(
                        f"  - `{item.get('object_name')}` ({item.get('type_id')} "
                        f"{item.get('guid')}{where})"
                    )
        audit = record.get("checksum_audit") or {}
        if audit:
            import_clean = (
                not audit.get("blank_count")
                and not audit.get("absent_count")
                and not audit.get("invalid_count")
            )
            lines.append(
                f"- Checksum status: `{'import-clean' if import_clean else 'NOT import-clean'}` "
                f"(bridge available: `{bool(audit.get('bridge_available'))}`, "
                f"recomputed: `{audit.get('recomputed_count', 0)}`, "
                f"blank: `{audit.get('blank_count', 0)}`, "
                f"absent: `{audit.get('absent_count', 0)}`, "
                f"invalid: `{audit.get('invalid_count', 0)}` of `{audit.get('checked_entries', 0)}` "
                "edited entries; preserved base entries keep valid checksums)."
            )
            invalid_entries = audit.get("invalid_entries") or []
            if invalid_entries:
                lines.append("  - Entries with invalid checksums (FluentControl will reject on load):")
                for entry in invalid_entries:
                    lines.append(f"    - `{entry}`")
            blank_entries = audit.get("blank_entries") or []
            if blank_entries:
                lines.append("  - Entries with blank checksums (FluentControl will reject/recalc on load):")
                for entry in blank_entries:
                    lines.append(f"    - `{entry}`")
            absent_entries = audit.get("absent_entries") or []
            if absent_entries:
                lines.append("  - Entries missing checksums (FluentControl will reject on load):")
                for entry in absent_entries:
                    lines.append(f"    - `{entry}`")
        archive_audit = record.get("archive_audit") or {}
        if archive_audit:
            arch_blocking = archive_audit.get("blocking") or []
            arch_needs_review = archive_audit.get("needs_review") or []
            status = "broken" if arch_blocking else ("needs-review" if arch_needs_review else "import-ready")
            lines.append(
                f"- Import artifact check: `{status}` "
                f"(zip OK: `{bool(archive_audit.get('zip_ok'))}`, "
                f"entries: `{archive_audit.get('entry_count', 0)}`, "
                f"blocking: `{len(arch_blocking)}`, needs-review: `{len(arch_needs_review)}`)."
            )
            if arch_blocking:
                lines.append("  - Blocking (the generated ZEIA will not load):")
                for item in arch_blocking:
                    lines.append(f"    - {_format_archive_finding(item)}")
            if arch_needs_review:
                lines.append("  - Needs review (loads, but confirm the dependency exists in the target):")
                if any(item.get("kind") == "unresolved_reference" for item in arch_needs_review):
                    lines.append(
                        "    - FluentControl may show a missing referenced files dialog during "
                        "import; install/import those dependencies first or confirm the warning "
                        "only when they already exist in the target system."
                    )
                for item in arch_needs_review:
                    lines.append(f"    - {_format_archive_finding(item)}")
        sub_audit = record.get("subroutine_audit") or {}
        added = sub_audit.get("added") or []
        if added:
            sub_blocking = sub_audit.get("blocking") or []
            status = "metadata-defect" if sub_blocking else "added-review"
            lines.append(
                f"- Subroutine additions: `{status}` "
                f"(added: `{len(added)}`, metadata defects: `{len(sub_blocking)}`)."
            )
            lines.append(
                "  - Prefer replace over add: a subroutine not present in the base ZEIA is added "
                "with synthesized datastore metadata, which is riskier than replacing an existing "
                "entry. For the safest import, build the subroutine into the base ZEIA in "
                "FluentControl first, then re-run so the pipeline replaces it (reusing its "
                "GUID/metadata)."
            )
            for item in sub_blocking:
                lines.append(f"  - Metadata defect: {_format_subroutine_finding(item)}")
        if record.get("warnings"):
            lines.append("- Warnings:")
            for warning in record["warnings"]:
                lines.append(f"  - {warning}")
        lines.append(f"- Checksum note: {record.get('checksum_note')}")
        lines.append(
            "- Next action: after import-clean checks pass, open the generated script in FluentControl Script Editor "
            "and resolve any missing variables, commands, labware, subroutines, adapters/fingers, or prompt-range errors before simulation or hardware use."
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _archive_script_records(archive_data: dict[str, bytes]) -> list[dict[str, Any]]:
    records = []
    for entry, data in archive_data.items():
        if not entry.lower().endswith(".xscr"):
            continue
        text = _decode_xml_bytes(data)
        records.append(
            {
                "entry": entry,
                "guid": _guid_from_archive_entry(entry),
                "object_name": _first_xml_text_from_text(text, "ObjectName"),
                "folder": _first_xml_text_from_text(text, "ObjectSubfolderPath"),
                "command_count": text.count("<Object Type="),
            }
        )
    return records


def _dependency_archive_data(
    primary_archive_data: dict[str, bytes],
    supplemental_archives: list[Path],
) -> dict[str, bytes]:
    archives = [primary_archive_data]
    for archive_path in _dedupe_paths(supplemental_archives):
        if not archive_path.exists() or archive_path.suffix.lower() != ".zeia" or not zipfile.is_zipfile(archive_path):
            continue
        with zipfile.ZipFile(archive_path, "r") as source_zip:
            supplemental = {info.filename: source_zip.read(info.filename) for info in source_zip.infolist()}
        if supplemental and supplemental is not primary_archive_data:
            archives.append(supplemental)
    if len(archives) == 1:
        return primary_archive_data

    combined = dict(primary_archive_data)
    for archive_data in archives[1:]:
        for entry, data in archive_data.items():
            normalized = _normalize_archive_entry(entry)
            if normalized in {"DataStore/nodedescription.xml", "meta/content.xml"}:
                continue
            if _find_archive_entry(combined, entry) is None:
                combined[entry] = data

    merged_node = _merged_nodedescription(archives)
    if merged_node:
        node_entry = _find_archive_entry(combined, "DataStore/nodedescription.xml") or "DataStore/nodedescription.xml"
        combined[node_entry] = merged_node
    return combined


def _merged_nodedescription(archives: list[dict[str, bytes]]) -> bytes:
    node_entries: list[tuple[str, bytes]] = []
    for archive_data in archives:
        entry = _find_archive_entry(archive_data, "DataStore/nodedescription.xml")
        if entry is not None:
            node_entries.append((entry, archive_data[entry]))
    if not node_entries:
        return b""

    base_text = _decode_xml_bytes(node_entries[0][1])
    seen_map_types = {
        _first_xml_text_from_text(block, "Type").casefold()
        for block in re.findall(r"<Map>\s*.*?</Map>", base_text, re.DOTALL)
        if _first_xml_text_from_text(block, "Type")
    }
    seen_ids = {
        _first_xml_text_from_text(block, "Id").casefold()
        for block in re.findall(r"<S\b[^>]*>.*?</S>", base_text, re.DOTALL)
        if _first_xml_text_from_text(block, "Id")
    }
    map_blocks: list[str] = []
    script_blocks: list[str] = []
    for _, data in node_entries[1:]:
        text = _decode_xml_bytes(data)
        for block in re.findall(r"<Map>\s*.*?</Map>", text, re.DOTALL):
            type_name = _first_xml_text_from_text(block, "Type")
            key = type_name.casefold()
            if not key or key in seen_map_types:
                continue
            seen_map_types.add(key)
            map_blocks.append(block)
        for block in re.findall(r"<S\b[^>]*>.*?</S>", text, re.DOTALL):
            guid = _first_xml_text_from_text(block, "Id")
            key = guid.casefold()
            if not key or key in seen_ids:
                continue
            seen_ids.add(key)
            script_blocks.append(block)

    merged = base_text
    if map_blocks and "</TypeMap>" in merged:
        merged = merged.replace("</TypeMap>", "".join(map_blocks) + "</TypeMap>", 1)
    if script_blocks and "</Payload>" in merged:
        merged = merged.replace("</Payload>", "".join(script_blocks) + "</Payload>", 1)
    if "<Checksum>" in merged:
        merged = _blank_checksum(merged)
    return _encode_xml_text_like(node_entries[0][1], merged)


def _archive_writer_dependency_records(
    archive_data: dict[str, bytes],
    *,
    root_guids: list[str],
    exclude_guids: set[str],
    skipped_import_unsupported: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return direct source datastore records needed by the generated script.

    The FluentControl archive writer can write a clean ZEIA as long as each
    referenced datastore object is provided as an existing file plus a matching
    metadata record. This function copies only objects already present in the
    source ZEIA; it never fabricates missing models or datastore metadata.
    """
    records_by_guid = _archive_nodedescription_records(archive_data)
    excluded = {guid.casefold() for guid in exclude_guids if guid}
    queued = [guid for guid in root_guids if guid and guid.casefold() not in excluded]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    while queued:
        guid = queued.pop(0)
        key = guid.casefold()
        if key in seen or key in excluded:
            continue
        seen.add(key)
        record = records_by_guid.get(key)
        if record is None:
            continue
        if _fluent_import_unsupported_dependency(record):
            if skipped_import_unsupported is not None:
                skipped_import_unsupported.append(_dependency_not_packaged_record(record))
            continue
        source_entry = str(record.get("source_entry") or "")
        if source_entry:
            record = dict(record)
            # The generated script needs its direct references to resolve during
            # import. Script dependencies can themselves reference worktables or
            # helper scripts, so follow .xscr payload references. Do not follow
            # non-script nodedescription edges: worktable/library graphs in full
            # exports are large and can surface unrelated target-library warnings.
            record["refs"] = []
            out.append(record)
            if source_entry.replace("\\", "/").casefold().endswith(".xscr"):
                for ref in _script_reference_guids_from_payload(archive_data[source_entry]):
                    if ref.casefold() not in seen and ref.casefold() not in excluded:
                        queued.append(ref)
    return out


def _fluent_import_unsupported_dependency(record: dict[str, Any]) -> bool:
    record_type = str(record.get("type") or "").strip()
    if record_type in FLUENT_IMPORT_UNSUPPORTED_DATASTORE_KEYS:
        return True
    if record_type in FLUENT_IMPORT_UNSUPPORTED_DATASTORE_TYPES:
        return True
    relative = str(record.get("relative_path") or record.get("source_entry") or "").replace("/", "\\").casefold()
    return any(
        token in relative
        for token in (
            "systemspecific\\liquidclasses\\",
            "systemspecific\\worktable\\components\\",
            "systemspecific\\worktable\\workspaces\\",
        )
    )


def _dependency_not_packaged_record(record: dict[str, Any]) -> dict[str, str]:
    record_type = str(record.get("type") or "")
    return {
        "guid": str(record.get("guid") or ""),
        "object_name": str(record.get("object_name") or ""),
        "type": _friendly_import_unsupported_type(record),
        "datastore_key": record_type if record_type in FLUENT_IMPORT_UNSUPPORTED_DATASTORE_KEYS else "",
        "relative_path": str(record.get("relative_path") or ""),
        "reason": "fluent_import_unsupported_datastore_key",
    }


def _friendly_import_unsupported_type(record: dict[str, Any]) -> str:
    record_type = str(record.get("type") or "").strip()
    if record_type and record_type not in FLUENT_IMPORT_UNSUPPORTED_DATASTORE_KEYS:
        return record_type
    relative = str(record.get("relative_path") or record.get("source_entry") or "").replace("/", "\\").casefold()
    if "systemspecific\\liquidclasses\\" in relative:
        return "LiquidClass"
    if "systemspecific\\worktable\\components\\" in relative:
        return "WorktableComponent"
    if "systemspecific\\worktable\\workspaces\\" in relative:
        return "WorktableWorkspace"
    return record_type or "datastore object"


def _archive_nodedescription_records(archive_data: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    node_entry = _find_archive_entry(archive_data, "DataStore/nodedescription.xml")
    if node_entry is None:
        return {}
    text = _decode_xml_bytes(archive_data[node_entry])
    entry_by_guid = _datastore_entries_by_guid(archive_data)
    type_by_short = {
        short.strip(): type_name.strip()
        for type_name, short in re.findall(
            r"<Map>\s*<Type>(.*?)</Type>\s*<Short>(.*?)</Short>\s*</Map>",
            text,
            re.DOTALL,
        )
    }
    records: dict[str, dict[str, Any]] = {}
    for block in re.findall(r"<S\b[^>]*>.*?</S>", text, re.DOTALL):
        guid = _first_xml_text_from_text(block, "Id")
        if not guid:
            continue
        source_entry = entry_by_guid.get(guid.casefold(), "")
        type_short = _first_xml_text_from_text(block, "T")
        attrs = re.search(r"<S\b([^>]*)>", block)
        attrs_text = attrs.group(1) if attrs else ""
        records[guid.casefold()] = {
            "guid": guid,
            "source_entry": source_entry,
            "relative_path": _datastore_relative_entry(source_entry) if source_entry else "",
            "object_name": _first_xml_text_from_text(block, "N"),
            "object_path": _first_xml_text_from_text(block, "P"),
            "type": type_by_short.get(type_short, type_short),
            "version": int(_first_xml_text_from_text(block, "V") or "1"),
            "type_version": _first_xml_text_from_text(block, "TV"),
            "is_root": 'isRootNode="True"' in attrs_text or "isRootNode='True'" in attrs_text,
            "was_manufacturer": 'wasMf="True"' in attrs_text or "wasMf='True'" in attrs_text,
            "refs": _unique_preserving_order(
                re.findall(r"<Ref>(.*?)</Ref>", block, re.DOTALL)
            ),
            "file_refs": _unique_preserving_order(
                re.findall(r"<FileRef>(.*?)</FileRef>", block, re.DOTALL)
            ),
        }
    return records


def _datastore_entries_by_guid(archive_data: dict[str, bytes]) -> dict[str, str]:
    entries: dict[str, str] = {}
    for entry in archive_data:
        normalized = entry.replace("\\", "/").casefold()
        if not normalized.startswith("datastore/"):
            continue
        if normalized.endswith("/nodedescription.xml"):
            continue
        guid = Path(entry.replace("\\", "/")).stem.casefold()
        if guid:
            entries.setdefault(guid, entry)
    return entries


def _find_datastore_entry_for_guid(archive_data: dict[str, bytes], guid: str) -> str | None:
    return _datastore_entries_by_guid(archive_data).get(guid.casefold())


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = value.strip()
        if not clean or clean.casefold() in seen:
            continue
        seen.add(clean.casefold())
        out.append(clean)
    return out


def _select_project_main_script(
    script_records: list[dict[str, Any]],
    *,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
    generated_name: str,
) -> dict[str, Any] | None:
    parent_paths = {Path(path).resolve() for path in [*source_scripts, *([source_xscr] if source_xscr is not None else [])]}
    if source_manifest and parent_paths:
        for script in source_manifest.get("scripts") or []:
            if not isinstance(script, dict):
                continue
            if _manifest_script_path(source_manifest, script) not in parent_paths:
                continue
            entry = str(script.get("entry") or "")
            match = _find_archive_script_by_entry(script_records, entry)
            if match is not None:
                return match
            object_name = str(script.get("object_name") or "")
            match = _find_archive_script_by_object_name(script_records, object_name)
            if match is not None:
                return match

    match = _find_archive_script_by_object_name(script_records, generated_name)
    if match is not None:
        return match
    if len(script_records) == 1:
        return script_records[0]
    non_subroutines = [
        item
        for item in script_records
        if not str(item.get("object_name") or "").casefold().startswith("sub_")
        and str(item.get("folder") or "").casefold() != "subroutines"
    ]
    candidates = non_subroutines or script_records
    return max(candidates, key=lambda item: int(item.get("command_count") or 0), default=None)


def _find_archive_script_by_entry(script_records: list[dict[str, Any]], entry: str) -> dict[str, Any] | None:
    normalized = _normalize_archive_entry(entry)
    if not normalized:
        return None
    for record in script_records:
        if _normalize_archive_entry(str(record.get("entry") or "")) == normalized:
            return record
    return None


def _find_archive_script_by_object_name(script_records: list[dict[str, Any]], object_name: str) -> dict[str, Any] | None:
    needle = object_name.casefold()
    if not needle:
        return None
    for record in script_records:
        if str(record.get("object_name") or "").casefold() == needle:
            return record
    return None


def _prepare_project_script_payload(
    path: Path,
    *,
    fallback_folder: str = "",
    target_folder: str | None = None,
) -> bytes:
    data = path.read_bytes()
    normalized_target = _normalize_script_folder(target_folder)
    folder = normalized_target or _normalize_script_folder(fallback_folder)
    if folder:
        data = _ensure_object_subfolder_path(data, folder, replace=bool(normalized_target))
    return data


def _normalize_script_folder(value: Any) -> str:
    return str(value or "").strip().strip("\\/")


def _ensure_object_subfolder_path(data: bytes, folder: str, *, replace: bool = False) -> bytes:
    if not folder:
        return data
    text = _decode_xml_bytes(data)
    existing_tag = re.search(r"<ObjectSubfolderPath>.*?</ObjectSubfolderPath>", text, flags=re.DOTALL)
    if existing_tag and not replace:
        return data
    tag = f"<ObjectSubfolderPath>{_xml_escape_text(folder)}</ObjectSubfolderPath>"
    if existing_tag:
        updated = re.sub(r"<ObjectSubfolderPath>.*?</ObjectSubfolderPath>", tag, text, count=1, flags=re.DOTALL)
    else:
        updated = text.replace("</ObjectName>", f"</ObjectName>\n    {tag}", 1)
    return _encode_xml_text_like(data, _blank_checksum(updated))


def _strip_unavailable_optional_references(
    data: bytes,
    archive_data: dict[str, bytes],
    *,
    source_label: str = "",
) -> tuple[bytes, list[dict[str, Any]]]:
    """Validate every model reference in a script against the source ZEIA base.

    The generated ZEIA reuses the original archive verbatim and only adds or
    replaces scripts, so each ``<Reference>`` block must resolve to a model
    (worktable, liquid class, carrier, device, rack, etc.) that already exists in
    the base. This never creates or fabricates a model. Instead:

    * a dead, unused ``LiquidClass`` reference whose GUID is absent is removed so
      the ZEIA still imports cleanly, and
    * every other unresolved reference (a used liquid class or any non-liquid
      model) is kept untouched and recorded as a structured finding so the
      missing-model dependency is surfaced in the import report rather than
      silently shipped.
    """
    text = _decode_xml_bytes(data)
    available_guids = _available_archive_guids(archive_data)
    findings: list[dict[str, Any]] = []
    liquid_class_usage = bool(re.search(r"<LiquidClassName(?:BySelection)?>(?!\s*</)", text))

    def _name_used(name: str) -> bool:
        if not name:
            return False
        return bool(
            re.search(
                r"<LiquidClassName[^>]*>\s*" + re.escape(name) + r"\s*</LiquidClassName",
                text,
                re.IGNORECASE,
            )
        )

    def replace(match: re.Match[str]) -> str:
        block = match.group(0)
        type_id = _first_xml_text_from_text(block, "TypeId")
        guid = _first_xml_text_from_text(block, "Guid")
        object_name = _first_xml_text_from_text(block, "ObjectName")
        if not guid or guid.casefold() in available_guids:
            return block
        finding: dict[str, Any] = {
            "type_id": type_id,
            "guid": guid,
            "object_name": object_name,
            "source_label": source_label,
        }
        if type_id == "LiquidClass" and not liquid_class_usage:
            finding["action"] = "removed"
            finding["used_in_script"] = False
            findings.append(finding)
            return ""
        finding["action"] = "retained_unresolved"
        finding["used_in_script"] = _name_used(object_name) if type_id == "LiquidClass" else True
        findings.append(finding)
        return block

    updated = re.sub(r"\s*<Reference>.*?</Reference>", replace, text, flags=re.DOTALL)
    if not any(item["action"] == "removed" for item in findings):
        return data, findings
    return _encode_xml_text_like(data, _blank_checksum(updated)), findings


def _available_archive_guids(archive_data: dict[str, bytes]) -> set[str]:
    guids = set()
    for entry, data in archive_data.items():
        guid = _guid_from_archive_entry(entry)
        if guid:
            guids.add(guid.casefold())
        if entry.replace("\\", "/").casefold().endswith("datastore/nodedescription.xml"):
            text = _decode_xml_bytes(data)
            guids.update(value.casefold() for value in re.findall(r"<Id>([0-9a-fA-F-]{36})</Id>", text))
    return guids


def _append_nodedescription_script_nodes(
    data: bytes,
    scripts: list[dict[str, str]],
    *,
    base_version: int = 1,
) -> bytes:
    text = _decode_xml_bytes(data)
    nodes = []
    for item in scripts:
        source_path = Path(item["source"])
        refs = _script_reference_guids(source_path)
        ref_lines = "".join(f"\t\t\t<Ref>{_xml_escape_text(ref)}</Ref>\r\n" for ref in refs)
        file_refs = _script_file_references(source_path)
        file_ref_lines = "".join(
            f"\t\t\t<FileRef>{_xml_escape_text(file_ref)}</FileRef>\r\n" for file_ref in file_refs
        )
        nodes.append(
            "\t\t<S isRootNode=\"True\">\r\n"
            f"\t\t\t<Id>{_xml_escape_text(item['guid'])}</Id>\r\n"
            f"\t\t\t<N>{_xml_escape_text(item['object_name'])}</N>\r\n"
            "\t\t\t<P></P>\r\n"
            "\t\t\t<T>2</T>\r\n"
            f"\t\t\t<V>{int(base_version)}</V>\r\n"
            "\t\t\t<TV>2.0</TV>\r\n"
            f"{ref_lines}"
            f"{file_ref_lines}"
            "\t\t</S>\r\n"
        )
    if not nodes:
        return data
    updated = text.replace("\t</Payload>", "".join(nodes) + "\t</Payload>", 1)
    return _encode_xml_text_like(data, _blank_checksum(updated))


def _next_nodedescription_version(text: str) -> int:
    """Return one past the highest existing per-node ``<V>`` value in a nodedescription.

    Real archives use incrementing ``<V>`` integers per node; new nodes should sit
    above every existing value so FluentControl does not surface version/sync
    warnings on import. Falls back to ``1`` when no ``<V>`` values are present.
    """
    values = [int(match) for match in re.findall(r"<V>(\d+)</V>", text)]
    if not values:
        return 1
    return max(values) + 1


def _append_content_datastore_entries(data: bytes, entries: list[str]) -> bytes:
    text = _decode_xml_bytes(data)
    additions = []
    for entry in entries:
        datastore_entry = _datastore_relative_entry(entry)
        if not datastore_entry or f"<Entry>{datastore_entry}</Entry>" in text:
            continue
        additions.append(f"\t\t\t<Entry>{_xml_escape_text(datastore_entry)}</Entry>\r\n")
    if not additions:
        return data
    if "\t\t\t<Entry>nodedescription.xml</Entry>" in text:
        updated = text.replace("\t\t\t<Entry>nodedescription.xml</Entry>", "".join(additions) + "\t\t\t<Entry>nodedescription.xml</Entry>", 1)
    else:
        updated = text.replace("\t\t</DatastoreEntries>", "".join(additions) + "\t\t</DatastoreEntries>", 1)
    return _encode_xml_text_like(data, _blank_checksum(updated))


def _update_nodedescription_script_identity(
    data: bytes,
    *,
    script_guid: str,
    object_name: str,
    folder: str,
) -> bytes | None:
    text = _decode_xml_bytes(data)
    for match in re.finditer(r"<S\b[^>]*>.*?</S>", text, flags=re.DOTALL):
        block = match.group(0)
        if not re.search(rf"<Id>{re.escape(script_guid)}</Id>", block):
            continue
        updated_block = _replace_or_insert_simple_tag(block, "N", object_name, after_tag="Id")
        if folder:
            updated_block = _replace_or_insert_simple_tag(updated_block, "P", folder, after_tag="N")
        if updated_block == block:
            return None
        updated = text[: match.start()] + updated_block + text[match.end() :]
        return _encode_xml_text_like(data, _blank_checksum(updated))
    return None


def _replace_or_insert_simple_tag(block: str, tag: str, value: str, *, after_tag: str) -> str:
    escaped = _xml_escape_text(value)
    if re.search(rf"<{tag}>.*?</{tag}>", block, flags=re.DOTALL):
        replacement = f"<{tag}>{escaped}</{tag}>"
        return re.sub(
            rf"<{tag}>.*?</{tag}>",
            lambda _match: replacement,
            block,
            count=1,
            flags=re.DOTALL,
        )
    return block.replace(f"</{after_tag}>", f"</{after_tag}>\r\n\t\t\t<{tag}>{escaped}</{tag}>", 1)


def _write_zip_info(out_zip: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes) -> None:
    out_zip.writestr(info, data)


def _restore_windows_datastore_zip_names(archive_path: Path) -> None:
    r"""Undo Python 3.14's slash normalization for FluentControl datastore entries.

    FluentControl ZEIA archives and their metadata conventionally use Windows
    separators for `DataStore\...` and `meta\...` member names. Python's zip
    writer normalizes those names to POSIX slashes, so patch only the filename
    bytes after writing; slash and backslash have the same length and do not
    affect compressed payloads or CRCs.
    """

    try:
        data = bytearray(archive_path.read_bytes())
    except OSError:
        return

    changed = False

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                local_header_offset = info.header_offset
                if data[local_header_offset : local_header_offset + 4] == b"PK\x03\x04":
                    name_len = int.from_bytes(
                        data[local_header_offset + 26 : local_header_offset + 28], "little"
                    )
                    filename = bytes(data[local_header_offset + 30 : local_header_offset + 30 + name_len])
                    rewritten = _windows_datastore_zip_filename(filename)

                    if rewritten is not None and rewritten != filename and len(rewritten) == len(filename):
                        data[local_header_offset + 30 : local_header_offset + 30 + name_len] = rewritten
                        changed = True

            cd_offset = getattr(zf, "start_dir", None)
            if cd_offset is not None:
                for _ in zf.infolist():
                    if cd_offset >= len(data):
                        break

                    if data[cd_offset : cd_offset + 4] != b"PK\x01\x02":
                        break

                    filename_len = int.from_bytes(data[cd_offset + 28 : cd_offset + 30], "little")
                    extra_len = int.from_bytes(data[cd_offset + 30 : cd_offset + 32], "little")
                    comment_len = int.from_bytes(data[cd_offset + 32 : cd_offset + 34], "little")

                    filename = bytes(data[cd_offset + 46 : cd_offset + 46 + filename_len])
                    rewritten = _windows_datastore_zip_filename(filename)

                    if rewritten is not None and rewritten != filename and len(rewritten) == len(filename):
                        data[cd_offset + 46 : cd_offset + 46 + filename_len] = rewritten
                        changed = True

                    cd_offset += 46 + filename_len + extra_len + comment_len

    except zipfile.BadZipFile:
        pass

    if changed:
        archive_path.write_bytes(data)


def _windows_datastore_zip_filename(filename: bytes) -> bytes | None:
    normalized = filename.replace(b"\\", b"/")
    lowered = normalized.lower()
    if lowered.startswith(b"datastore/") or lowered.startswith(b"meta/"):
        return normalized.replace(b"/", b"\\")
    return None


def _find_archive_entry(archive_data: dict[str, bytes], expected: str) -> str | None:
    normalized = _normalize_archive_entry(expected)
    for entry in archive_data:
        if _normalize_archive_entry(entry) == normalized:
            return entry
    return None


def _archive_has_datastore_metadata(archive_data: dict[str, bytes]) -> bool:
    return _find_archive_entry(archive_data, "DataStore/nodedescription.xml") is not None and _find_archive_entry(archive_data, "meta/content.xml") is not None


def _datastore_relative_entry(entry: str) -> str:
    normalized = entry.replace("/", "\\")
    prefix = "DataStore\\"
    if normalized.casefold().startswith(prefix.casefold()):
        return normalized[len(prefix) :]
    return normalized


def _unique_archive_entry(entry: str, archive_data: dict[str, bytes], additions: dict[str, bytes]) -> str:
    existing = {_normalize_archive_entry(name) for name in archive_data}
    existing.update(_normalize_archive_entry(name) for name in additions)
    if _normalize_archive_entry(entry) not in existing:
        return entry
    path = Path(entry.replace("\\", "/"))
    stem = path.stem
    suffix = path.suffix
    parent = str(path.parent).replace("/", "\\")
    for index in range(2, 1000):
        candidate = f"{parent}\\{stem}_{index}{suffix}" if parent != "." else f"{stem}_{index}{suffix}"
        if _normalize_archive_entry(candidate) not in existing:
            return candidate
    raise PipelineError(f"could not create a unique archive entry for {entry}")


def _stable_project_guid(source_project: Path, object_name: str, path: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_project.resolve()}::{object_name}::{path.resolve()}"))


def _fluentcontrol_userspecific_dirs() -> list[Path]:
    """Return candidate FluentControl UserSpecific datastore directories."""
    from .fluentcontrol_inventory import fluentcontrol_userspecific_dirs

    return fluentcontrol_userspecific_dirs()


def _find_local_fluentcontrol_script_guid(
    object_name: str,
    folder: str,
    *,
    userspecific_dir: Path | None = None,
) -> str | None:
    """Return the installed GUID for the same script name+folder, if present.

    FluentControl import matches ``ObjectSubfolderPath\\ObjectName`` and remaps a
    packaged new GUID onto the local object before ``LoadData`` checksum
    validation. Packaging must reuse that GUID and restamp checksums, or import
    fails with ``VX_APPFR_016_005``.
    """
    return find_local_script_guid(
        object_name,
        folder,
        userspecific_dir=userspecific_dir,
    )


def _unique_project_guid(
    source_project: Path,
    object_name: str,
    path: Path,
    existing_guids: set[str],
) -> str:
    """Return a deterministic uuid5 GUID that does not collide with the archive.

    Mirrors :func:`_stable_project_guid` for the common (no-collision) case so the
    same inputs reproduce the same GUID across runs. If the base GUID already
    exists in ``existing_guids`` (datastore entry filenames plus nodedescription
    ``<Id>`` values, all casefolded), salt the uuid5 input with an incrementing
    counter and re-check until a free GUID is found.
    """
    base = _stable_project_guid(source_project, object_name, path)
    if base.casefold() not in existing_guids:
        return base
    for salt in range(1, 10000):
        candidate = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{source_project.resolve()}::{object_name}::{path.resolve()}::collision-{salt}",
            )
        )
        if candidate.casefold() not in existing_guids:
            return candidate
    raise PipelineError(
        f"could not derive a non-colliding datastore GUID for subroutine `{object_name}`"
    )


def _guid_from_archive_entry(entry: str) -> str:
    stem = Path(entry.replace("\\", "/")).stem
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", stem):
        return stem
    return ""


def _normalize_archive_entry(entry: str) -> str:
    return entry.replace("\\", "/").casefold().strip("/")


def _script_object_name_from_path(path: Path) -> str:
    if not path.exists():
        return ""
    return _first_xml_text_from_text(_decode_xml_bytes(path.read_bytes()), "ObjectName")


def _script_folder_from_path(path: Path) -> str:
    if not path.exists():
        return ""
    return _first_xml_text_from_text(_decode_xml_bytes(path.read_bytes()), "ObjectSubfolderPath")


def _script_reference_guids(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = _decode_xml_bytes(path.read_bytes())
    guids = []
    for block in re.findall(r"<Reference>.*?</Reference>", text, flags=re.DOTALL):
        guid = _first_xml_text_from_text(block, "Guid")
        if guid and guid not in guids:
            guids.append(guid)
    return guids


def _script_file_references(path: Path) -> list[str]:
    """Extract external-file dependencies declared by a script.

    Real ``.xscr`` payloads list binary/asset deps as ``<FileReference><File>``
    absolute paths (images, ``.exe`` helpers, ``.vb`` scripts), which archives
    mirror verbatim as ``<FileRef>`` entries on the script's nodedescription node.
    """
    if not path.exists():
        return []
    text = _decode_xml_bytes(path.read_bytes())
    files: list[str] = []
    for block in re.findall(r"<FileReference>.*?</FileReference>", text, flags=re.DOTALL):
        value = _first_xml_text_from_text(block, "File")
        if value and value not in files:
            files.append(value)
    return files


def _folder_from_subroutine_ref(item: dict[str, Any]) -> str:
    ref = str(item.get("ref") or "")
    ref = ref.strip().strip('"').replace("/", "\\")
    if "\\" in ref:
        return ref.split("\\", 1)[0]
    return ""


def _first_xml_text_from_text(text: str, name: str) -> str:
    match = re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, flags=re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _decode_xml_bytes(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def _normalize_archive_writer_script_payload(data: bytes) -> bytes:
    text = _decode_xml_bytes(data)
    text = re.sub(r'dataVersion="\d+"', 'dataVersion="1"', text, count=1)
    return _encode_xml_text_like(data, text)


def _postprocess_archive_writer_script_payload(data: bytes) -> bytes:
    text = _decode_xml_bytes(data)
    text = re.sub(r'dataVersion="\d+"', 'dataVersion="1"', text, count=1)
    text = _localize_variable_definition_helper_namespaces(text)
    text = _ensure_blank_checksum_element(text)
    return _encode_xml_text_like(data, text)


def _localize_variable_definition_helper_namespaces(text: str) -> str:
    localized, _ = localize_variable_declaration_namespaces(text)
    return localized


def _ensure_blank_checksum_element(text: str) -> str:
    if re.search(r"<Checksum>.*?</Checksum>", text, flags=re.DOTALL):
        return text
    if re.search(r"<Checksum\s*/>", text):
        return re.sub(r"<Checksum\s*/>", "<Checksum></Checksum>", text, count=1)
    return re.sub(
        r'(\s*</(?:[A-Za-z_][\w.-]*:)?VxData>\s*)$',
        "\n  <Checksum></Checksum>\\1",
        text,
        count=1,
    )


def _find_archive_entry(entries: list[str], wanted: str) -> str | None:
    normalized = _normalize_archive_entry(wanted)
    for entry in entries:
        if _normalize_archive_entry(entry) == normalized:
            return entry
    return None


def _replace_zip_entry(archive_path: Path, target_entry: str, data: bytes) -> None:
    tmp_path = archive_path.with_suffix(archive_path.suffix + ".tmp")
    with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(tmp_path, "w") as dest:
        replaced = False
        for info in source.infolist():
            payload = data if info.filename == target_entry else source.read(info.filename)
            dest.writestr(info, payload)
            replaced = replaced or info.filename == target_entry
        if not replaced:
            dest.writestr(target_entry, data, compress_type=zipfile.ZIP_DEFLATED)
    tmp_path.replace(archive_path)
    _restore_windows_datastore_zip_names(archive_path)


def _script_folder_from_payload(data: bytes) -> str:
    return _first_xml_text_from_text(_decode_xml_bytes(data), "ObjectSubfolderPath")


def _script_type_version_from_payload(data: bytes) -> str:
    text = _decode_xml_bytes(data)
    match = re.search(r'<Script\s+version="([^"]+)"', text, re.DOTALL)
    return match.group(1).strip() if match else "2.0"


def _script_reference_guids_from_payload(data: bytes) -> list[str]:
    values = []
    for value in re.findall(r"<Guid>(.*?)</Guid>", _decode_xml_bytes(data), re.DOTALL):
        guid = value.strip()
        if guid and guid != "00000000-0000-0000-0000-000000000000":
            values.append(guid)
    return values


def _script_file_refs(data: bytes) -> list[str]:
    return [
        value.strip()
        for value in re.findall(r"<File>(.*?)</File>", _decode_xml_bytes(data), re.DOTALL)
        if value.strip()
    ]


def _fluent_archive_writer_available() -> bool:
    return (
        _ARCHIVE_WRITER_SHARED_DLL.exists()
        and _ARCHIVE_WRITER_IMPL_DLL.exists()
        and (_powershell_executable() is not None)
    )


_PORTABLE_TYPE_SHORT_DEFAULTS = {
    "Configuration": "1",
    "VolatileConfiguration": "2",
    "Method": "3",
    "Script": "4",
    "WorktableComponent": "5",
    "WorktableConnector": "6",
    "WorktableMesh": "7",
    "WorktableTexture": "8",
    "WorktableWorkspace": "9",
    "CustomAttributeTemplateList": "10",
    "WorktableSite": "11",
    "LiquidClass": "12",
    "LiquidClassParameterSetExtension": "13",
    "UndoRedoHistory": "14",
    "DriverConfiguration": "15",
}


def _portable_type_short_map(records: list[dict[str, Any]]) -> dict[str, str]:
    """Map datastore type names to nodedescription short ids."""
    mapping = dict(_PORTABLE_TYPE_SHORT_DEFAULTS)
    used = {value for value in mapping.values()}
    next_short = 16
    for record in records:
        type_name = str(record.get("type") or "Script").strip() or "Script"
        if type_name in mapping:
            continue
        while str(next_short) in used:
            next_short += 1
        mapping[type_name] = str(next_short)
        used.add(str(next_short))
        next_short += 1
    return mapping


def _portable_checksummed_xml(text: str) -> bytes:
    payload = text.encode("utf-8")
    recomputed = recompute_checksum_bytes(payload)
    return recomputed if recomputed is not None else payload


def _build_portable_nodedescription_xml(
    records: list[dict[str, Any]],
    type_shorts: dict[str, str],
) -> bytes:
    map_lines = []
    for type_name, short in sorted(type_shorts.items(), key=lambda item: int(item[1]) if item[1].isdigit() else item[1]):
        map_lines.append(
            "\t\t\t<Map>\r\n"
            f"\t\t\t\t<Type>{_xml_escape_text(type_name)}</Type>\r\n"
            f"\t\t\t\t<Short>{_xml_escape_text(short)}</Short>\r\n"
            "\t\t\t</Map>\r\n"
        )
    node_lines = []
    for record in records:
        type_name = str(record.get("type") or "Script").strip() or "Script"
        short = type_shorts.get(type_name, type_shorts.get("Script", "4"))
        refs = "".join(
            f"\t\t\t<Ref>{_xml_escape_text(str(ref))}</Ref>\r\n"
            for ref in (record.get("refs") or [])
            if str(ref or "").strip()
        )
        file_refs = "".join(
            f"\t\t\t<FileRef>{_xml_escape_text(str(file_ref))}</FileRef>\r\n"
            for file_ref in (record.get("file_refs") or [])
            if str(file_ref or "").strip()
        )
        root_attr = ' isRootNode="True"' if bool(record.get("is_root")) else ""
        mf_attr = ' wasMf="True"' if bool(record.get("was_manufacturer")) else ""
        node_lines.append(
            f"\t\t<S{root_attr}{mf_attr}>\r\n"
            f"\t\t\t<Id>{_xml_escape_text(str(record.get('guid') or ''))}</Id>\r\n"
            f"\t\t\t<N>{_xml_escape_text(str(record.get('object_name') or ''))}</N>\r\n"
            f"\t\t\t<P>{_xml_escape_text(str(record.get('object_path') or ''))}</P>\r\n"
            f"\t\t\t<T>{_xml_escape_text(short)}</T>\r\n"
            f"\t\t\t<V>{int(record.get('version') or 1)}</V>\r\n"
            f"\t\t\t<TV>{_xml_escape_text(str(record.get('type_version') or '2.0'))}</TV>\r\n"
            f"{refs}"
            f"{file_refs}"
            "\t\t</S>\r\n"
        )
    text = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        "<NodeDescription>\r\n"
        "\t<Payload>\r\n"
        "\t\t<MappingSection>\r\n"
        f"{''.join(map_lines)}"
        "\t\t</MappingSection>\r\n"
        f"{''.join(node_lines)}"
        "\t</Payload>\r\n"
        "\t<Checksum></Checksum>\r\n"
        "</NodeDescription>\r\n"
    )
    return _portable_checksummed_xml(text)


def _build_portable_content_xml(datastore_entries: list[str]) -> bytes:
    entry_lines = []
    seen: set[str] = set()
    for entry in datastore_entries:
        relative = _datastore_relative_entry(entry)
        if not relative:
            continue
        key = relative.casefold()
        if key in seen:
            continue
        seen.add(key)
        entry_lines.append(f"\t\t\t<Entry>{_xml_escape_text(relative)}</Entry>\r\n")
    if "nodedescription.xml" not in seen:
        entry_lines.append("\t\t\t<Entry>nodedescription.xml</Entry>\r\n")
    text = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        "<ArchiveContent>\r\n"
        "\t<Payload>\r\n"
        "\t\t<FilesystemEntries>\r\n"
        "\t\t</FilesystemEntries>\r\n"
        "\t\t<DatastoreEntries>\r\n"
        f"{''.join(entry_lines)}"
        "\t\t</DatastoreEntries>\r\n"
        "\t</Payload>\r\n"
        "\t<Checksum></Checksum>\r\n"
        "</ArchiveContent>\r\n"
    )
    return _portable_checksummed_xml(text)


def _build_portable_sysinfo_xml() -> bytes:
    export_date = datetime.now(timezone.utc).isoformat()
    text = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        "<SystemInfo>\r\n"
        "\t<Payload>\r\n"
        "\t\t<ExportVersion>1</ExportVersion>\r\n"
        "\t\t<SoftwareVersion>portable.archive.writer</SoftwareVersion>\r\n"
        "\t\t<OSName>portable</OSName>\r\n"
        "\t\t<OSEdition>tecan-protocol-builder</OSEdition>\r\n"
        "\t\t<OSBitSize>Bit64</OSBitSize>\r\n"
        "\t\t<DomainUser></DomainUser>\r\n"
        "\t\t<UmsUser></UmsUser>\r\n"
        f"\t\t<ExportDate>{_xml_escape_text(export_date)}</ExportDate>\r\n"
        "\t</Payload>\r\n"
        "\t<Checksum></Checksum>\r\n"
        "</SystemInfo>\r\n"
    )
    return _portable_checksummed_xml(text)


def _run_portable_archive_writer(
    *,
    script_path: Path,
    archive_path: Path,
    datastore_root: Path,
    metadata_json: Path,
) -> dict[str, Any]:
    """Write a script-scoped ZEIA without FluentControl Windows assemblies.

    Consumes the same staged datastore root + metadata JSON as the Fluent writer.
    """
    del script_path  # parity with Fluent writer signature; payload comes from metadata paths
    try:
        records = json.loads(metadata_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"portable archive writer could not read metadata JSON: {exc}") from exc
    if not isinstance(records, list) or not records:
        raise PipelineError("portable archive writer requires a non-empty metadata record list")

    ensure_parent(archive_path)
    if archive_path.exists():
        archive_path.unlink()

    type_shorts = _portable_type_short_map(records)
    archive_entries: dict[str, bytes] = {}
    datastore_entry_names: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise PipelineError("portable archive writer metadata records must be objects")
        relative = str(record.get("relative_path") or "").replace("/", "\\").strip()
        if not relative:
            raise PipelineError("portable archive writer metadata record is missing relative_path")
        staged = datastore_root / Path(relative.replace("\\", "/"))
        if not staged.is_file():
            raise PipelineError(f"portable archive writer missing staged datastore file: {relative}")
        archive_entries[f"DataStore\\{relative}"] = staged.read_bytes()
        datastore_entry_names.append(relative)

    archive_entries["DataStore\\nodedescription.xml"] = _build_portable_nodedescription_xml(
        records, type_shorts
    )
    archive_entries["meta\\content.xml"] = _build_portable_content_xml(datastore_entry_names)
    archive_entries["meta\\sysinfo.xml"] = _build_portable_sysinfo_xml()

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zip:
        for name, payload in archive_entries.items():
            out_zip.writestr(name, payload)
    _restore_windows_datastore_zip_names(archive_path)

    return {
        "archive": str(archive_path),
        "success": True,
        "exceptionOccurred": False,
        "exceptionMessage": "",
        "successMessage": "portable archive writer completed",
        "numberOfDataobjectsInserted": len(records),
        "warningMessages": [],
        "errorMessages": [],
        "packaging_method": "portable_archive_writer",
    }


def _powershell_executable() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _run_fluent_archive_writer(
    *,
    script_path: Path,
    archive_path: Path,
    datastore_root: Path,
    metadata_json: Path,
) -> dict[str, Any]:
    shell = _powershell_executable()
    if shell is None:
        raise _ArchiveWriterUnavailable("PowerShell is not available")
    try:
        timeout_seconds = max(1, int(os.environ.get("TECAN_ARCHIVE_WRITER_TIMEOUT_SECONDS") or "300"))
    except ValueError:
        timeout_seconds = 300
    writer_script = metadata_json.parent / "write_script_only_archive.ps1"
    writer_script.write_text(_ARCHIVE_WRITER_PS1, encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(writer_script),
                "-ScriptPath",
                str(script_path),
                "-ArchivePath",
                str(archive_path),
                "-DatastoreRoot",
                str(datastore_root),
                "-MetadataJson",
                str(metadata_json),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise _ArchiveWriterUnavailable(
            f"FluentControl archive writer timed out after {timeout_seconds} seconds"
        ) from exc
    if completed.returncode != 0:
        raise PipelineError(
            "FluentControl archive writer failed: "
            + (completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}")
        )
    output = completed.stdout.strip()
    try:
        report = json.loads(output[output.find("{") :]) if "{" in output else {}
    except json.JSONDecodeError:
        report = {"raw_stdout": output}
    if report and not report.get("success", False):
        raise PipelineError(f"FluentControl archive writer failed: {report}")
    return report


_ARCHIVE_WRITER_PS1 = r'''
param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,
    [string]$DatastoreRoot = "",
    [string]$MetadataJson = ""
)

$ErrorActionPreference = "Stop"
$shared = "C:\Program Files (x86)\Common Files\Tecan\Core\Recent\Tecan.VisionX.ExportImportArchive.Shared.dll"
$impl = "C:\Program Files (x86)\Tecan\FluentControl\Tecan.VisionX.ExportImportArchive.dll"
Add-Type -Path $shared
Add-Type -Path $impl
$netstandard = @(
    "$([System.Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory())\Facades\netstandard.dll",
    "$([System.Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory())\netstandard.dll",
    "C:\Program Files\dotnet\packs\NETStandard.Library.Ref\2.1.0\ref\netstandard2.1\netstandard.dll",
    "C:\Program Files\dotnet\packs\NETStandard.Library.Ref\2.1.0\ref\netstandard2.0\netstandard.dll"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

$factorySource = @"
using Tecan.VisionX.ExportImportArchive;
using Tecan.VisionX.ExportImportArchive.Shared;
using Tecan.VisionX.ExportImportArchive.Shared.DI;
using Tecan.VisionX.ExportImportArchive.Shared.Interfaces;
public sealed class CursorArchiveObjectsFactory : IArchiveObjectsFactory, IFileAccessTestObjectsFactory
{
    public IMetaDirectoryNode CreateMetaDirectoryNodeForWrite(string softwareVersion, string umsUserName) { return new MetaDirectoryNode(this, softwareVersion, umsUserName); }
    public IMetaDirectoryNode CreateMetaDirectoryNodeForRead() { return new MetaDirectoryNode(this); }
    public IRegistryDirectoryNode CreateRegistryDirectoryNode() { return new RegistryDirectoryNode(this); }
    public IDatastoreDirectoryNode CreateDataStoreDirectoryNode() { return new DatastoreDirectoryNode(this, this); }
    public IFilesystemDirectoryNode CreateFilesystemDirectoryNode() { return new FilesystemDirectoryNode(this, this); }
    public IExtendedFileSystemNode CreateExtendedFileSystemNode() { return new ExtendedFileSystemNode(this); }
    public IEntryData CreateEntryData(string value) { return new EntryData(value); }
    public IDirectoryMapData CreateDirectoryMapData(string shortCut, string fullDirectoryName) { return new DirectoryMapData(shortCut, fullDirectoryName); }
    public IExportInfoData CreateExportInfoData() { return new ExportInfoData(); }
    public IOperatingSystemVersionProvider CreateOperatingSystemVersionProvider() { return new OperatingSystemVersionProvider(); }
    public ICoreEntryDescriptor CreateCoreEntryDescriptor(string archivePath) { return new CoreEntryDescriptor(archivePath); }
    public IMappedEntryDescriptor CreateMappedEntryDescriptor(string archivePath, string sourcePath) { return new MappedEntryDescriptor(archivePath, sourcePath); }
    public IFileWriteAccessTester CreateFileWriteAccessTester() { return new FileWriteAccessTester(this); }
    public IWriteAccessTestResult CreateWriteAccessTestResult() { return new WriteAccessTestResult(); }
}
"@

$references = @($shared, $impl)
if ($netstandard) { $references += $netstandard }
Add-Type -TypeDefinition $factorySource -ReferencedAssemblies $references
if (Test-Path $ArchivePath) { Remove-Item $ArchivePath -Force }

$factory = [CursorArchiveObjectsFactory]::new()
$writer = [Tecan.VisionX.ExportImportArchive.VxExportImportArchiveWriter]::new($ArchivePath, "3.7.12.125", "Cursor", $factory)
if ($DatastoreRoot) { $writer.DatastoreDirectoryNode.RootDatastorePath = $DatastoreRoot }

function Add-DatastoreRecord($record) {
    $data = [Tecan.VisionX.ExportImportArchive.Shared.DatastoreTransferData]::new()
    $data.RelativeFilepath = [string]$record.relative_path
    $data.ObjectName = [string]$record.object_name
    $data.ObjectPath = [string]$record.object_path
    $data.TypeStr = [string]$record.type
    $data.UniqueId = [guid]$record.guid
    $data.DataVersion = [uint64]$record.version
    $data.IsRootExportnode = [bool]$record.is_root
    $data.WasManufacturerObject = [bool]$record.was_manufacturer
    $data.TypespecificVersionStr = [string]$record.type_version
    foreach ($ref in @($record.refs)) {
        $value = [string]$ref
        if ($value) { $data.ReferencedObjects.Add([guid]$value) }
    }
    foreach ($fileRef in @($record.file_refs)) {
        $value = [string]$fileRef
        if ($value) { $data.FileReferences.Add($value) }
    }
    $writer.DatastoreDirectoryNode.AddDataSet($data)
}

$records = Get-Content -LiteralPath $MetadataJson -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($record in @($records)) { Add-DatastoreRecord $record }
$result = $writer.WriteArchive()
[pscustomobject]@{
    archive = $ArchivePath
    script = $ScriptPath
    success = $result.Success
    exceptionOccurred = $result.ExceptionOccured
    exceptionMessage = $result.ExceptionMessage
    successMessage = $result.SuccessMessage
    numberOfDataobjectsInserted = $result.NumberOfDataobjectsInserted
    warningMessages = $result.WarningMessages
    errorMessages = $result.ErrorMessages
} | ConvertTo-Json -Depth 6
'''


def _encode_xml_text_like(original: bytes, text: str) -> bytes:
    if original.startswith(b"\xef\xbb\xbf"):
        return text.encode("utf-8-sig")
    return text.encode("utf-8")


def _blank_checksum(text: str) -> str:
    return re.sub(r"<Checksum>.*?</Checksum>", "<Checksum></Checksum>", text, count=1, flags=re.DOTALL)


def _xml_escape_text(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _resolved_subroutine_artifacts(
    source_manifest: dict[str, Any] | None,
    *,
    parent_scripts: list[Path],
) -> list[dict[str, Any]]:
    if not source_manifest or not parent_scripts:
        return []
    parent_paths = {Path(path).resolve() for path in parent_scripts if path is not None}
    if not parent_paths:
        return []
    scripts = [script for script in source_manifest.get("scripts") or [] if isinstance(script, dict)]
    parent_records = [
        script
        for script in scripts
        if _manifest_script_path(source_manifest, script) in parent_paths
    ]
    if not parent_records:
        parent_names = {path.name.casefold() for path in parent_paths}
        parent_records = [
            script
            for script in scripts
            if Path(str(script.get("entry") or script.get("extracted_path") or "")).name.casefold() in parent_names
        ]
    out = []
    seen: set[Path] = set()
    parent_resolved_paths = {_manifest_script_path(source_manifest, script) for script in parent_records}
    queue = list(parent_records)
    scanned: set[Path] = set()
    while queue:
        parent = queue.pop(0)
        parent_path = _manifest_script_path(source_manifest, parent)
        if parent_path in scanned:
            continue
        scanned.add(parent_path)
        deps = parent.get("dependencies") or {}
        for ref in deps.get("subroutine_refs") or []:
            match, alternatives = _find_subroutine_record(source_manifest, scripts, str(ref), parent)
            if not match:
                continue
            path = _manifest_script_path(source_manifest, match)
            if path in seen or path in parent_resolved_paths or not path.exists():
                continue
            seen.add(path)
            queue.append(match)
            out.append(
                {
                    "ref": str(ref),
                    "object_name": str(match.get("object_name") or ""),
                    "folder": str(match.get("folder") or match.get("object_subfolder_path") or ""),
                    "guid": str(match.get("guid") or match.get("script_guid") or ""),
                    "entry": str(match.get("entry") or ""),
                    "version": str(match.get("script_version") or match.get("version") or ""),
                    "source_context": str(match.get("source_context") or ""),
                    "path": path,
                    "ambiguous": bool(alternatives),
                    "alternatives": alternatives,
                }
            )
    return out


def _dedupe_subroutine_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    seen_identities: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        path_text = str(item.get("path") or "").strip()
        path = Path(path_text).resolve() if path_text else None
        if path is not None and path in seen_paths:
            continue
        identity = _subroutine_artifact_identity(item)
        if identity and identity in seen_identities:
            continue
        deduped.append(item)
        if path is not None:
            seen_paths.add(path)
        if identity:
            seen_identities.add(identity)
    return deduped


def _subroutine_artifact_identity(item: dict[str, Any]) -> str:
    qualified = str(item.get("qualified_name") or "").strip()
    if qualified:
        return norm_subroutine_key(qualified)
    object_name = str(item.get("object_name") or item.get("name") or "").strip()
    folder = str(item.get("folder") or item.get("object_subfolder_path") or "").strip().strip("\\/")
    if object_name:
        return norm_subroutine_key(f"{folder}\\{object_name}" if folder else object_name)
    ref = str(item.get("ref") or "").strip()
    if ref:
        return norm_subroutine_key(ref)
    entry = str(item.get("entry") or "").strip()
    if entry:
        return norm_subroutine_key(Path(entry.replace("\\", "/")).stem)
    path = str(item.get("path") or "").strip()
    return norm_subroutine_key(Path(path).stem) if path else ""


def _manifest_script_path(source_manifest: dict[str, Any], script: dict[str, Any]) -> Path:
    return _manifest_item_path(source_manifest, script)


def _find_subroutine_record(
    source_manifest: dict[str, Any],
    scripts: list[dict[str, Any]],
    ref: str,
    parent: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Resolve a subroutine reference to a manifest script record.

    Returns ``(match, alternatives)``. ``alternatives`` is non-empty only when
    the reference remains genuinely ambiguous after match-strength ranking and
    context/root preference. In that case a deterministic record is chosen so
    packaging stays stable, and the skipped candidates are returned so callers
    can warn that the wrong subroutine might have been packaged.
    """
    return _resolve_subroutine_record(source_manifest, scripts, ref, parent)


def _subroutine_match_strength(script: dict[str, Any], forms: dict[str, str]) -> int:
    """Rank how strongly a script matches a subroutine reference (0 = no match).

    Higher is a more reliable identity match so that, for example, an exact
    object-name match always wins over a loose filename-stem collision.
    """
    object_name = str(script.get("object_name") or "").casefold()
    qualified = str(script.get("qualified_name") or "").replace("\\", "/").casefold()
    entry = str(script.get("entry") or "").replace("\\", "/").casefold()
    extracted = str(script.get("extracted_path") or "").replace("\\", "/").casefold()
    entry_name = Path(entry).name if entry else ""
    extracted_name = Path(extracted).name if extracted else ""
    entry_stem = Path(entry).stem if entry else ""
    extracted_stem = Path(extracted).stem if extracted else ""

    clean = forms["clean"]
    normalized_path = forms["normalized_path"]
    name = forms["name"]
    stem = forms["stem"]

    if object_name and object_name in {clean, name}:
        return 4
    if qualified and qualified in {clean, normalized_path}:
        return 4
    if normalized_path and normalized_path in {entry, extracted}:
        return 3
    if name and name in {entry_name, extracted_name}:
        return 2
    if stem and stem in {entry_stem, extracted_stem}:
        return 1
    return 0


def _subroutine_artifact_name(item: dict[str, Any], index: int) -> str:
    label = item.get("object_name") or Path(str(item.get("entry") or item["path"].stem)).stem
    return f"subroutine_{index}_{_safe_label(str(label))}.xscr"


def _render_subroutine_manifest(records: list[dict[str, Any]]) -> str:
    lines = [
        "# Subroutine Scripts",
        "",
        "These FluentControl subroutine scripts were resolved from the parent source script and packaged as first-class artifacts.",
        "",
    ]
    if any(record.get("ambiguous") for record in records):
        lines.extend(
            [
                "> Some references below matched more than one source script. The most "
                "likely match was packaged, but verify the ambiguous entries against the "
                "source project before import.",
                "",
            ]
        )
    for index, record in enumerate(records, start=1):
        lines.append(f"{index}. `{record.get('object_name') or record.get('ref')}`")
        lines.append(f"   - Reference: `{record.get('ref')}`")
        lines.append(f"   - Bundle file: `{record.get('relative_path')}`")
        if record.get("folder"):
            lines.append(f"   - Source folder: `{record.get('folder')}`")
        if record.get("guid"):
            lines.append(f"   - Source GUID: `{record.get('guid')}`")
        if record.get("version"):
            lines.append(f"   - Source version: `{record.get('version')}`")
        if record.get("entry"):
            lines.append(f"   - Source entry: `{record.get('entry')}`")
        if record.get("source_context"):
            lines.append(f"   - Source context: `{record.get('source_context')}`")
        if record.get("ambiguous"):
            alternatives = ", ".join(
                f"`{alt.get('object_name') or alt.get('entry') or '?'}`"
                + (f" ({alt.get('source_context')})" if alt.get("source_context") else "")
                for alt in record.get("alternatives") or []
            )
            lines.append("   - Ambiguous: `True`")
            lines.append(f"   - Skipped alternative(s): {alternatives or '`unknown`'}")
    return "\n".join(lines).rstrip() + "\n"


def _resolved_hardware_artifacts(
    source_manifest: dict[str, Any] | None,
    *,
    script_paths: list[Path],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": HARDWARE_MANIFEST_SCHEMA_VERSION,
        "status": "no_hardware_refs",
        "summary": {},
        "scripts": [],
        "pins": [],
        "assets": [],
        "barcode_refs": [],
        "touchtools_titles": [],
        "connector_artifacts": [],
        "asset_artifacts": [],
        "notes": [
            "Static ZEIA evidence can support compatibility checks but cannot verify or reconfigure physical instrument pins.",
        ],
    }
    if not source_manifest or not script_paths:
        return report

    script_records = _manifest_scripts_for_paths(source_manifest, script_paths)
    if not script_records:
        return report

    pin_refs: set[str] = set()
    asset_refs: set[str] = set()
    barcode_refs: set[str] = set()
    touchtools_titles: set[str] = set()
    script_summaries: list[dict[str, Any]] = []
    for script in script_records:
        deps = script.get("dependencies") or {}
        script_pins = _sorted_strs([*(deps.get("pin_refs") or []), *(deps.get("worktable_pin_locations") or [])])
        script_assets = _sorted_strs(deps.get("custom_asset_refs") or [])
        script_barcodes = _sorted_strs(deps.get("barcode_refs") or [])
        script_touchtools = _sorted_strs(deps.get("touchtools_titles") or [])
        pin_refs.update(script_pins)
        asset_refs.update(script_assets)
        barcode_refs.update(script_barcodes)
        touchtools_titles.update(script_touchtools)
        if script_pins or script_assets or script_barcodes or script_touchtools:
            script_summaries.append(
                {
                    "object_name": str(script.get("object_name") or ""),
                    "entry": str(script.get("entry") or ""),
                    "source_context": str(script.get("source_context") or ""),
                    "source_path": str(_manifest_script_path(source_manifest, script)),
                    "pin_refs": script_pins,
                    "asset_refs": script_assets,
                    "barcode_refs": script_barcodes,
                    "touchtools_titles": script_touchtools,
                }
            )

    objects = [obj for obj in source_manifest.get("objects") or [] if isinstance(obj, dict)]
    connector_objects = [
        obj
        for obj in objects
        if str(obj.get("kind") or "").casefold() == "connector" and (obj.get("pin_refs") or obj.get("object_name"))
    ]
    asset_objects = [
        obj
        for obj in objects
        if str(obj.get("kind") or "").casefold() == "asset"
    ]

    connector_artifacts: dict[str, dict[str, Any]] = {}
    pin_records: list[dict[str, Any]] = []
    for pin in sorted(pin_refs):
        matches = [obj for obj in connector_objects if _object_matches_pin(obj, pin)]
        connector_refs = []
        for obj in matches:
            record = _hardware_object_record(source_manifest, obj, "connector")
            connector_artifacts[record["key"]] = record
            connector_refs.append(_artifact_reference(record))
        pin_records.append(
            {
                "pin_ref": pin,
                "status": _pin_status(pin, connector_refs),
                "connector_artifacts": connector_refs,
                "manual_verification_required": True,
            }
        )

    asset_artifacts: dict[str, dict[str, Any]] = {}
    asset_records: list[dict[str, Any]] = []
    asset_names = sorted({name for ref in asset_refs for name in _asset_names_from_ref(ref)})
    for name in asset_names:
        matches = [obj for obj in asset_objects if _object_matches_asset(obj, name)]
        asset_refs_for_name = _asset_raw_refs_for_name(asset_refs, name)
        packaged_refs = []
        for obj in matches:
            record = _hardware_object_record(source_manifest, obj, "asset")
            asset_artifacts[record["key"]] = record
            packaged_refs.append(_artifact_reference(record))
        asset_records.append(
            {
                "asset_name": name,
                "raw_refs": asset_refs_for_name,
                "status": "asset_packaged" if packaged_refs else "referenced_but_not_packaged",
                "asset_artifacts": packaged_refs,
                "manual_verification_required": not bool(packaged_refs),
            }
        )

    report.update(
        {
            "status": "static_evidence_packaged" if (pin_records or asset_records) else "no_hardware_refs",
            "scripts": script_summaries,
            "pins": pin_records,
            "assets": asset_records,
            "barcode_refs": sorted(barcode_refs),
            "touchtools_titles": sorted(touchtools_titles),
            "connector_artifacts": list(connector_artifacts.values()),
            "asset_artifacts": list(asset_artifacts.values()),
        }
    )
    report["summary"] = _hardware_summary(report)
    return report


def _has_hardware_evidence(report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    return bool(report.get("pins") or report.get("assets") or report.get("connector_artifacts") or report.get("asset_artifacts"))


def _write_labware_catalog_artifact(
    source_manifest: dict[str, Any] | None,
    *,
    source_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> Path | None:
    """Persist ZEIA-derived labware catalog into the bundle source tree.

    Prefer detailed ``worktable_geometry`` components; for large exports that skip
    geometry, walk ``Components/*.xcmp`` under the extracted DataStore.
    """
    from .labware_catalog_export import LABWARE_CATALOG_FILENAME, write_labware_catalog

    manifest = source_manifest if isinstance(source_manifest, dict) else {}
    geometry = manifest.get("worktable_geometry") if isinstance(manifest.get("worktable_geometry"), dict) else None
    context_root = None
    for key in ("root", "context_root", "extracted_dir"):
        raw = manifest.get(key)
        if raw:
            context_root = Path(str(raw))
            break
    destination = source_dir / LABWARE_CATALOG_FILENAME
    written = write_labware_catalog(
        destination,
        geometry,
        source="worktable_geometry",
        context_root=context_root or source_dir.parent,
    )
    if written is None:
        return None
    exports.append(ExportedArtifact(written, written, "labware-catalog"))
    copied_files.append(_file_record("labware-catalog", written, written, bundle_root=bundle_root))
    return written


def _write_connector_coverage_artifact(
    source_manifest: dict[str, Any] | None,
    *,
    source_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> Path | None:
    """Persist ZEIA-derived connector coverage (name profiles → resolved GUIDs/counts)."""
    from .connector_coverage_export import CONNECTOR_COVERAGE_FILENAME, write_connector_coverage

    geometry = (source_manifest or {}).get("worktable_geometry") if isinstance(source_manifest, dict) else None
    if not isinstance(geometry, dict) or not geometry.get("components"):
        return None
    destination = source_dir / CONNECTOR_COVERAGE_FILENAME
    written = write_connector_coverage(destination, geometry, source="worktable_geometry")
    if written is None:
        return None
    exports.append(ExportedArtifact(written, written, "connector-coverage"))
    copied_files.append(_file_record("connector-coverage", written, written, bundle_root=bundle_root))
    return written


def _write_connector_graph_artifact(
    source_manifest: dict[str, Any] | None,
    *,
    source_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> Path | None:
    """Persist ZEIA-derived connector snap graph into the bundle source tree.

    Prefer full ``Connectors/*.xcon`` Snap walk under the source extract/DataStore
    when present so package graphs match host rebuild scope (large exports often
    skip detailed ``worktable_geometry``).
    """
    from .connector_graph_export import CONNECTOR_GRAPH_FILENAME, write_connector_graph

    manifest = source_manifest if isinstance(source_manifest, dict) else {}
    geometry = manifest.get("worktable_geometry") if isinstance(manifest.get("worktable_geometry"), dict) else None
    context_root = None
    for key in ("root", "context_root", "extracted_dir"):
        raw = manifest.get(key)
        if raw:
            context_root = Path(str(raw))
            break
    destination = source_dir / CONNECTOR_GRAPH_FILENAME
    written = write_connector_graph(
        destination,
        geometry,
        source="worktable_geometry",
        context_root=context_root or source_dir.parent,
    )
    if written is None:
        return None
    exports.append(ExportedArtifact(written, written, "connector-graph"))
    copied_files.append(_file_record("connector-graph", written, written, bundle_root=bundle_root))
    return written


def _write_liquid_classes_artifact(
    source_manifest: dict[str, Any] | None,
    *,
    source_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> Path | None:
    """Persist ZEIA-derived liquid class catalog (``*.xlqc``) into the bundle source tree."""
    from .liquid_classes_export import LIQUID_CLASSES_FILENAME, write_liquid_classes_catalog

    manifest = source_manifest if isinstance(source_manifest, dict) else {}
    context_root = None
    for key in ("root", "context_root", "extracted_dir"):
        raw = manifest.get(key)
        if raw:
            context_root = Path(str(raw))
            break
    destination = source_dir / LIQUID_CLASSES_FILENAME
    written = write_liquid_classes_catalog(
        destination,
        manifest=manifest,
        context_root=context_root or source_dir.parent,
        source="zeia_xlqc",
    )
    if written is None:
        return None
    exports.append(ExportedArtifact(written, written, "liquid-classes"))
    copied_files.append(_file_record("liquid-classes", written, written, bundle_root=bundle_root))
    return written


def _write_driver_macros_artifact(
    source_manifest: dict[str, Any] | None,
    *,
    source_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> Path | None:
    """Persist ZEIA-mined Legacy/Application driver macro inventory (soft-empty OK)."""
    from .driver_macros_export import DRIVER_MACROS_FILENAME, write_driver_macros_catalog

    manifest = source_manifest if isinstance(source_manifest, dict) else {}
    context_root = None
    for key in ("root", "context_root", "extracted_dir"):
        raw = manifest.get(key)
        if raw:
            context_root = Path(str(raw))
            break
    destination = source_dir / DRIVER_MACROS_FILENAME
    written = write_driver_macros_catalog(
        destination,
        manifest=manifest,
        context_root=context_root or source_dir.parent,
        source="zeia_scripts",
    )
    if written is None:
        return None
    exports.append(ExportedArtifact(written, written, "driver-macros"))
    copied_files.append(_file_record("driver-macros", written, written, bundle_root=bundle_root))
    return written


def _write_script_folder_bindings_artifact(
    source_manifest: dict[str, Any] | None,
    *,
    source_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> Path | None:
    """Persist Scripts-folder tree + script→worktable bindings from ZEIA manifest."""
    from .script_folder_bindings_export import (
        SCRIPT_FOLDER_BINDINGS_FILENAME,
        write_script_folder_bindings,
    )

    manifest = source_manifest if isinstance(source_manifest, dict) else {}
    destination = source_dir / SCRIPT_FOLDER_BINDINGS_FILENAME
    written = write_script_folder_bindings(destination, manifest, source="zeia_scripts")
    if written is None:
        return None
    exports.append(ExportedArtifact(written, written, "script-folder-bindings"))
    copied_files.append(
        _file_record("script-folder-bindings", written, written, bundle_root=bundle_root)
    )
    return written


def _has_packaged_hardware_connectors(report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    return any(bool(record.get("packaged")) for record in report.get("connector_artifacts") or [])


def _write_hardware_artifacts(
    report: dict[str, Any],
    *,
    script_dir: Path,
    hardware_dir: Path,
    direct_connectors_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> dict[str, Any]:
    hardware_dir.mkdir(parents=True, exist_ok=True)
    connector_bundle_paths: dict[str, str] = {}
    asset_bundle_paths: dict[str, str] = {}

    for index, record in enumerate(report.get("connector_artifacts") or [], start=1):
        source = Path(str(record.get("source_path") or ""))
        if not source.exists():
            record["packaged"] = False
            record["missing_source"] = True
            continue
        destination = direct_connectors_dir / _hardware_connector_artifact_name(record, index)
        _copy_record(source, destination, "hardware-connector", exports, copied_files, bundle_root=bundle_root)
        record["packaged"] = True
        record["bundle_path"] = _bundle_relative_path(destination, bundle_root=bundle_root)
        connector_bundle_paths[str(record.get("key") or record.get("source_path") or "")] = record["bundle_path"]

    assets_dir = hardware_dir / "assets"
    for index, record in enumerate(report.get("asset_artifacts") or [], start=1):
        source = Path(str(record.get("source_path") or ""))
        if not source.exists():
            record["packaged"] = False
            record["missing_source"] = True
            continue
        destination = assets_dir / _hardware_asset_artifact_name(record, index)
        _copy_record(source, destination, "hardware-asset", exports, copied_files, bundle_root=bundle_root)
        record["packaged"] = True
        record["bundle_path"] = _bundle_relative_path(destination, bundle_root=bundle_root)
        asset_bundle_paths[str(record.get("key") or record.get("source_path") or "")] = record["bundle_path"]

    for pin in report.get("pins") or []:
        for connector in pin.get("connector_artifacts") or []:
            bundle_path = connector_bundle_paths.get(str(connector.get("key") or ""))
            if bundle_path:
                connector["bundle_path"] = bundle_path
    for asset in report.get("assets") or []:
        for artifact in asset.get("asset_artifacts") or []:
            bundle_path = asset_bundle_paths.get(str(artifact.get("key") or ""))
            if bundle_path:
                artifact["bundle_path"] = bundle_path

    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["summary"] = _hardware_summary(report)

    manifest_dest = hardware_dir / "hardware_manifest.json"
    manifest_dest.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    exports.append(ExportedArtifact(manifest_dest, manifest_dest, "hardware-manifest"))
    copied_files.append(_file_record("hardware-manifest", manifest_dest, manifest_dest, bundle_root=bundle_root))

    checklist_dest = script_dir / "HARDWARE_PINS.md"
    checklist_dest.write_text(_render_hardware_pins_checklist(report), encoding="utf-8")
    exports.append(ExportedArtifact(checklist_dest, checklist_dest, "hardware-pins-checklist"))
    copied_files.append(_file_record("hardware-pins-checklist", checklist_dest, checklist_dest, bundle_root=bundle_root))
    return report


def _manifest_scripts_for_paths(source_manifest: dict[str, Any], script_paths: list[Path]) -> list[dict[str, Any]]:
    wanted_paths = {Path(path).resolve() for path in script_paths if path is not None}
    if not wanted_paths:
        return []
    scripts = [script for script in source_manifest.get("scripts") or [] if isinstance(script, dict)]
    matches = [script for script in scripts if _manifest_script_path(source_manifest, script) in wanted_paths]
    if matches:
        return _dedupe_manifest_records(matches)

    wanted_names = {Path(path).name.casefold() for path in wanted_paths}
    name_matches = [
        script
        for script in scripts
        if Path(str(script.get("entry") or script.get("extracted_path") or "").replace("\\", "/")).name.casefold()
        in wanted_names
    ]
    return _dedupe_manifest_records(name_matches)


def _manifest_item_path(source_manifest: dict[str, Any], item: dict[str, Any]) -> Path:
    raw = item.get("resolved_path") or item.get("extracted_path") or item.get("entry") or ""
    normalized = str(raw).replace("\\", "/")
    path = Path(normalized).expanduser()
    if path.is_absolute():
        return path.resolve()
    root = Path(str(item.get("context_root") or source_manifest.get("root") or "")).expanduser()
    if root:
        return (root / path).resolve()
    return path.resolve()


def _dedupe_manifest_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen: set[str] = set()
    for record in records:
        key = str(record.get("resolved_path") or record.get("extracted_path") or record.get("entry") or id(record))
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


def _sorted_strs(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def _object_matches_pin(obj: dict[str, Any], pin: str) -> bool:
    candidates = [
        *(str(value) for value in obj.get("pin_refs") or []),
        str(obj.get("object_name") or ""),
        str(obj.get("description") or ""),
    ]
    return any(_pin_refs_match(pin, candidate) for candidate in candidates if candidate)


def _pin_refs_match(required: str, candidate: str) -> bool:
    required_norm = _pin_norm(required)
    candidate_norm = _pin_norm(candidate)
    if not required_norm or not candidate_norm:
        return False
    if required_norm == candidate_norm or required_norm in candidate_norm or candidate_norm in required_norm:
        return True
    required_suffix = _pin_suffix(required)
    candidate_suffix = _pin_suffix(candidate)
    return bool(required_suffix and candidate_suffix and required_suffix == candidate_suffix)


def _pin_norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _pin_suffix(value: str) -> str:
    match = re.search(r"(?:WorktablePin_|Pin)([A-Za-z0-9_]+)", value)
    if not match:
        return ""
    return re.sub(r"[^a-z0-9]+", "", match.group(1).casefold())


def _pin_status(pin: str, connector_refs: list[dict[str, str]]) -> str:
    if connector_refs:
        return "connector_evidence_packaged"
    if str(pin).casefold().startswith("gio"):
        return "runtime_pin_verification_required"
    return "no_static_connector_evidence"


def _asset_names_from_ref(ref: object) -> list[str]:
    text = str(ref or "").strip().strip('"').strip("'")
    if not text:
        return []
    names: set[str] = set()
    normalized = text.replace("\\", "/")
    path = Path(normalized)
    if path.suffix.casefold() in ASSET_SUFFIXES:
        names.add(path.name)
    for match in re.findall(r"[A-Za-z0-9_. -]+\.(?:bmp|gif|jpe?g|png|tiff?)", normalized, flags=re.IGNORECASE):
        names.add(Path(match).name)
    return sorted(names)


def _asset_raw_refs_for_name(raw_refs: set[str], name: str) -> list[str]:
    return sorted(ref for ref in raw_refs if name.casefold() in {item.casefold() for item in _asset_names_from_ref(ref)})


def _object_matches_asset(obj: dict[str, Any], name: str) -> bool:
    candidates = [
        str(obj.get("object_name") or ""),
        str(obj.get("entry") or ""),
        str(obj.get("extracted_path") or ""),
        *(str(value) for value in obj.get("asset_refs") or []),
    ]
    wanted = name.casefold()
    for candidate in candidates:
        if wanted in {item.casefold() for item in _asset_names_from_ref(candidate)}:
            return True
        if Path(candidate.replace("\\", "/")).name.casefold() == wanted:
            return True
    return False


def _hardware_object_record(source_manifest: dict[str, Any], obj: dict[str, Any], kind: str) -> dict[str, Any]:
    source_path = _manifest_item_path(source_manifest, obj)
    return {
        "key": str(source_path),
        "kind": kind,
        "object_name": str(obj.get("object_name") or ""),
        "entry": str(obj.get("entry") or ""),
        "source_context": str(obj.get("source_context") or ""),
        "source_path": str(source_path),
        "pin_refs": _sorted_strs(obj.get("pin_refs") or []),
        "asset_refs": _sorted_strs(obj.get("asset_refs") or []),
        "component_guid": str(obj.get("component_guid") or ""),
        "site_guid": str(obj.get("site_guid") or ""),
        "description": str(obj.get("description") or ""),
    }


def _artifact_reference(record: dict[str, Any]) -> dict[str, str]:
    return {
        "key": str(record.get("key") or ""),
        "object_name": str(record.get("object_name") or ""),
        "entry": str(record.get("entry") or ""),
    }


def _hardware_connector_artifact_name(record: dict[str, Any], index: int) -> str:
    source = Path(str(record.get("source_path") or ""))
    suffix = source.suffix or ".xcon"
    label = _hardware_file_label(source.stem or record.get("object_name") or "connector")
    return f"connector_{index}_{label}{suffix}"


def _hardware_asset_artifact_name(record: dict[str, Any], index: int) -> str:
    source = Path(str(record.get("source_path") or ""))
    suffix = source.suffix or Path(str(record.get("object_name") or "")).suffix or ".asset"
    label = _hardware_file_label(source.stem or Path(str(record.get("object_name") or "asset")).stem)
    return f"asset_{index}_{label}{suffix}"


def _hardware_file_label(value: object, limit: int = 80) -> str:
    label = _safe_label(str(value or "item"))
    if len(label) > limit:
        label = label[:limit].rstrip(".-_")
    return label or "item"


def _hardware_summary(report: dict[str, Any]) -> dict[str, Any]:
    pins = report.get("pins") or []
    assets = report.get("assets") or []
    connectors = report.get("connector_artifacts") or []
    asset_artifacts = report.get("asset_artifacts") or []
    return {
        "script_count": len(report.get("scripts") or []),
        "required_pin_count": len(pins),
        "pins_with_connector_evidence": sum(1 for pin in pins if pin.get("connector_artifacts")),
        "runtime_or_unresolved_pin_count": sum(
            1
            for pin in pins
            if pin.get("status") in {"runtime_pin_verification_required", "no_static_connector_evidence"}
        ),
        "asset_ref_count": len(assets),
        "packaged_asset_ref_count": sum(1 for asset in assets if asset.get("asset_artifacts")),
        "connector_artifact_count": len(connectors),
        "packaged_connector_artifact_count": sum(1 for item in connectors if item.get("packaged")),
        "asset_artifact_count": len(asset_artifacts),
        "packaged_asset_artifact_count": sum(1 for item in asset_artifacts if item.get("packaged")),
        "barcode_ref_count": len(report.get("barcode_refs") or []),
        "touchtools_title_count": len(report.get("touchtools_titles") or []),
    }


def _render_hardware_pins_checklist(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Hardware Pins and Custom Parts",
        "",
        "This package includes static ZEIA evidence for pin-located hardware, custom connector files, and custom detail assets when they could be resolved.",
        "",
        "It does not reconfigure physical pins or approve an instrument run. Confirm these items in FluentControl and against the instrument before use.",
        "",
        "## Summary",
        "",
        f"- Required pin refs: `{summary.get('required_pin_count', 0)}`",
        f"- Pins with packaged connector evidence: `{summary.get('pins_with_connector_evidence', 0)}`",
        f"- Runtime/unresolved pin refs: `{summary.get('runtime_or_unresolved_pin_count', 0)}`",
        f"- Custom asset refs: `{summary.get('asset_ref_count', 0)}`",
        f"- Packaged connector files: `{summary.get('packaged_connector_artifact_count', 0)}`",
        f"- Packaged asset files: `{summary.get('packaged_asset_artifact_count', 0)}`",
        "",
    ]

    pins = report.get("pins") or []
    if pins:
        lines.extend(["## Pin Checks", ""])
        for item in pins:
            bundle_paths = [
                str(ref.get("bundle_path") or ref.get("entry") or ref.get("object_name") or "")
                for ref in item.get("connector_artifacts") or []
            ]
            bundle_paths = [value for value in bundle_paths if value]
            lines.append(f"- `{item.get('pin_ref')}`")
            lines.append(f"  - Static status: `{item.get('status')}`")
            if bundle_paths:
                lines.append(f"  - Connector evidence: `{', '.join(bundle_paths[:8])}`")
                if len(bundle_paths) > 8:
                    lines.append(f"  - Additional connector evidence: `{len(bundle_paths) - 8}` more in `hardware/connectors/`")
            else:
                lines.append("  - Connector evidence: `none packaged`")
            lines.append("  - Manual check: confirm this pin/site in FluentControl's worktable or hardware configuration.")
        lines.append("")

    assets = report.get("assets") or []
    if assets:
        lines.extend(["## Custom Detail Assets", ""])
        for item in assets:
            bundle_paths = [
                str(ref.get("bundle_path") or ref.get("entry") or ref.get("object_name") or "")
                for ref in item.get("asset_artifacts") or []
            ]
            bundle_paths = [value for value in bundle_paths if value]
            lines.append(f"- `{item.get('asset_name')}`")
            lines.append(f"  - Static status: `{item.get('status')}`")
            if bundle_paths:
                lines.append(f"  - Packaged asset: `{', '.join(bundle_paths[:8])}`")
            else:
                lines.append("  - Packaged asset: `none found in ZEIA extraction`")
            raw_refs = item.get("raw_refs") or []
            if raw_refs:
                lines.append(f"  - Referenced as: `{', '.join(str(ref) for ref in raw_refs[:4])}`")
        lines.append("")

    barcode_refs = report.get("barcode_refs") or []
    if barcode_refs:
        lines.extend(["## Barcode References", ""])
        for ref in barcode_refs[:20]:
            lines.append(f"- `{ref}`")
        if len(barcode_refs) > 20:
            lines.append(f"- `{len(barcode_refs) - 20}` additional barcode refs are listed in `hardware/hardware_manifest.json`.")
        lines.append("")

    touchtools_titles = report.get("touchtools_titles") or []
    if touchtools_titles:
        lines.extend(["## TouchTools Screens", ""])
        for title in touchtools_titles[:20]:
            lines.append(f"- `{title}`")
        if len(touchtools_titles) > 20:
            lines.append(f"- `{len(touchtools_titles) - 20}` additional titles are listed in `hardware/hardware_manifest.json`.")
        lines.append("")

    lines.extend(
        [
            "## FluentControl Verification Steps",
            "",
            "1. Open the same FluentControl project/workspace context used by the source export.",
            "2. Confirm each listed worktable pin/site exists and matches the packaged connector evidence.",
            "3. Confirm any `GIO*_Pin*` refs in the instrument hardware configuration; these are runtime I/O pins, not worktable connector files.",
            "4. Confirm each custom detail image is available on the FluentControl runtime machine or copied from `hardware/assets/`.",
            "5. Run the optional FluentControl import/load diagnostic or manually open the generated artifact in Script Editor, then review logs before any physical run.",
            "",
            "Machine-readable evidence is in `hardware/hardware_manifest.json`.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _method_touchtools_readiness_report(
    compiled_xscr: Path,
    *,
    source_manifest: dict[str, Any] | None,
    script_paths: list[Path],
    request_spec: Path | None,
) -> dict[str, Any]:
    output = _xscr_readiness_evidence(compiled_xscr, role="generated_output")
    source_scripts = _source_script_readiness_evidence(source_manifest, script_paths)

    all_titles = sorted(
        {
            title
            for title in [
                *(output.get("touchtools_titles") or []),
                *[
                    item
                    for script in source_scripts
                    for item in script.get("touchtools_titles") or []
                ],
            ]
            if str(title or "").strip()
        }
    )
    all_prompts = [
        *(output.get("operator_prompts") or []),
        *[
            prompt
            for script in source_scripts
            for prompt in script.get("operator_prompts") or []
        ],
    ]
    startup_variables = _readiness_startup_variables([output, *source_scripts])
    touchtools_requested = _request_mentions_touchtools(request_spec)
    touchtools_workflow = bool(touchtools_requested or all_titles)
    output_artifact_type = _artifact_type_for_path(compiled_xscr)
    method_required = output_artifact_type == "script" and touchtools_workflow
    touchtools_visibility_required = touchtools_workflow
    status = (
        "method_preparation_required"
        if method_required
        else "touchtools_method_check_required"
        if touchtools_visibility_required
        else "direct_script_review"
    )

    report = {
        "schema_version": METHOD_TOUCHTOOLS_SCHEMA_VERSION,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "output": {
            "path": str(compiled_xscr),
            "filename": compiled_xscr.name,
            "artifact_type": output_artifact_type,
            "object_name": output.get("object_name") or compiled_xscr.stem,
            "touchtools_titles": output.get("touchtools_titles") or [],
            "operator_prompt_count": len(output.get("operator_prompts") or []),
        },
        "decision": {
            "touchtools_requested_by_spec": touchtools_requested,
            "touchtools_workflow_detected": touchtools_workflow,
            "method_required_before_touchtools": method_required,
            "touchtools_visibility_required": touchtools_visibility_required,
            "reason": _method_touchtools_reason(
                output_artifact_type,
                touchtools_requested=touchtools_requested,
                touchtools_titles=all_titles,
            ),
        },
        "summary": {
            "source_script_count": len(source_scripts),
            "touchtools_title_count": len(all_titles),
            "operator_prompt_count": len(all_prompts),
            "startup_variable_review_count": len(startup_variables),
        },
        "touchtools_titles": all_titles,
        "startup_variables": startup_variables,
        "operator_prompts": _dedupe_operator_prompts(all_prompts),
        "source_scripts": source_scripts,
        "manual_checks": _method_touchtools_manual_checks(
            method_required=method_required,
            touchtools_visibility_required=touchtools_visibility_required,
            startup_variable_count=len(startup_variables),
            operator_prompt_count=len(all_prompts),
        ),
    }
    return report


def _write_method_touchtools_artifacts(
    report: dict[str, Any],
    *,
    script_dir: Path,
    reports_dir: Path,
    bundle_root: Path,
    exports: list[ExportedArtifact],
    copied_files: list[dict[str, str]],
) -> None:
    json_dest = reports_dir / "method_touchtools_readiness.json"
    ensure_parent(json_dest)
    json_dest.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    exports.append(ExportedArtifact(json_dest, json_dest, "method-touchtools-readiness-json"))
    copied_files.append(_file_record("method-touchtools-readiness-json", json_dest, json_dest, bundle_root=bundle_root))

    report_dest = script_dir / "METHOD_TOUCHTOOLS_READINESS.md"
    report_dest.write_text(_render_method_touchtools_readiness(report), encoding="utf-8")
    exports.append(ExportedArtifact(report_dest, report_dest, "method-touchtools-readiness"))
    copied_files.append(_file_record("method-touchtools-readiness", report_dest, report_dest, bundle_root=bundle_root))


def _source_script_readiness_evidence(
    source_manifest: dict[str, Any] | None,
    script_paths: list[Path],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    manifest_records = _manifest_scripts_for_paths(source_manifest, script_paths) if source_manifest else []
    for record in manifest_records:
        path = _manifest_script_path(source_manifest or {}, record)
        key = str(path.resolve()) if path.exists() else str(record.get("entry") or id(record))
        seen.add(key)
        out.append(_manifest_script_readiness_evidence(source_manifest or {}, record, path))

    for path in script_paths:
        if path is None:
            continue
        resolved = Path(path).resolve()
        key = str(resolved)
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        out.append(_xscr_readiness_evidence(resolved, role="source_script"))
    return out


def _manifest_script_readiness_evidence(
    source_manifest: dict[str, Any],
    record: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    parsed = _xscr_readiness_evidence(path, role="source_script") if path.exists() else {}
    dependencies = record.get("dependencies") or {}
    return {
        "role": "source_script",
        "path": str(path),
        "entry": str(record.get("entry") or ""),
        "source_context": str(record.get("source_context") or ""),
        "object_name": str(record.get("object_name") or parsed.get("object_name") or ""),
        "artifact_type": "script",
        "touchtools_titles": _sorted_strs(
            [
                *(dependencies.get("touchtools_titles") or []),
                *(parsed.get("touchtools_titles") or []),
            ]
        ),
        "startup_variables": _dedupe_startup_variables(
            [
                *(record.get("startup_variables") or []),
                *(parsed.get("startup_variables") or []),
            ]
        ),
        "operator_prompts": _dedupe_operator_prompts(
            [
                *(record.get("operator_prompts") or []),
                *(parsed.get("operator_prompts") or []),
            ]
        ),
    }


def _xscr_readiness_evidence(path: Path, *, role: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "role": role,
        "path": str(path),
        "object_name": path.stem,
        "artifact_type": _artifact_type_for_path(path),
        "touchtools_titles": [],
        "startup_variables": [],
        "operator_prompts": [],
    }
    root = _parse_xml_safely(path)
    if root is None:
        return evidence
    evidence["object_name"] = _first_text(root, "ObjectName") or path.stem
    evidence["touchtools_titles"] = _sorted_strs(
        [el.text for el in root.iter() if _local_name(el.tag) == "RUPScreenTitle" and el.text]
    )
    evidence["startup_variables"] = _xscr_startup_variables(root)
    evidence["operator_prompts"] = _xscr_operator_prompts(root)
    return evidence


def _parse_xml_safely(path: Path) -> ET.Element | None:
    if path is None or not path.exists():
        return None
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return None


def _xscr_startup_variables(root: ET.Element) -> list[dict[str, Any]]:
    variables = []
    seen: set[tuple[str, str, str]] = set()
    for el in root.iter():
        direct_names = {_local_name(child.tag) for child in list(el)}
        if not {"Name", "TypeName", "QueryOnStartup"}.issubset(direct_names):
            continue
        name = _direct_text(el, "Name")
        if not name:
            continue
        record = {
            "name": name,
            "scope": _direct_text(el, "Scope") or "",
            "type": _direct_text(el, "TypeName") or "",
            "query_on_startup": _direct_bool(el, "QueryOnStartup"),
            "prompt": _direct_text(el, "QueryOnStartupString") or "",
            "read_only": _direct_bool(el, "ReadOnly"),
            "default_values": _direct_values(el, "Values"),
        }
        record["manual_review_required"] = bool(record["query_on_startup"] or record["prompt"])
        key = (str(record["name"]), str(record["scope"]), str(record["type"]))
        if key in seen:
            continue
        seen.add(key)
        variables.append(record)
    return variables


def _xscr_operator_prompts(root: ET.Element) -> list[dict[str, Any]]:
    prompts = []
    for el in root.iter():
        statement_name = _local_name(el.tag)
        if statement_name not in {"RUPVariableStatement", "RUPWorktableStatement", "RUPStandardStatement"}:
            continue
        variables = _xscr_rup_variable_items(el)
        prompt = {
            "kind": statement_name,
            "title": _first_text(el, "RUPScreenTitle") or "",
            "line_number": _first_text(el, "LineNumber") or "",
            "instructions": _first_text(el, "Instructions") or "",
            "display_and_wait": _first_text(el, "RUPDisplayAndWait") or "",
            "auto_close": _first_text(el, "RUPAutoClose") or "",
            "timeout": _first_text(el, "RUPTimeOut") or "",
            "variables": variables,
        }
        if prompt["title"] or prompt["instructions"] or variables:
            prompts.append(prompt)
    return prompts


def _xscr_rup_variable_items(statement: ET.Element) -> list[dict[str, str]]:
    items = []
    for item in statement.iter():
        if _local_name(item.tag) != "RupVariableItem":
            continue
        record = {
            "name": _direct_text(item, "VariableName") or "",
            "display_text": _direct_text(item, "DisplayText") or "",
            "display_type": _direct_text(item, "DisplayType") or "",
            "allowed_values": _direct_text(item, "AllowedValues") or "",
            "enabled": _direct_text(item, "IsEnabled") or "",
        }
        if any(record.values()):
            items.append(record)
    return items


def _direct_bool(parent: ET.Element, name: str) -> bool:
    return (_direct_text(parent, name) or "").casefold() == "true"


def _direct_values(parent: ET.Element, name: str) -> list[str]:
    values_node = _direct_child(parent, name)
    if values_node is None:
        return []
    return [
        child.text.strip()
        for child in list(values_node)
        if child.text and child.text.strip()
    ]


def _artifact_type_for_path(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".xscr":
        return "script"
    if suffix in {".xmet", ".xmth", ".xmethod"}:
        return "method"
    return suffix.lstrip(".") or "unknown"


def _request_mentions_touchtools(request_spec: Path | None) -> bool:
    if request_spec is None or not request_spec.exists():
        return False
    try:
        text = request_spec.read_text(encoding="utf-8").casefold()
    except (OSError, UnicodeDecodeError):
        return False
    needles = [
        "touchtools",
        "touch tools",
        "touchscreen",
        "touch screen",
        "touch monitor",
        "method starter",
        "system care",
        "maintenance method",
    ]
    return any(needle in text for needle in needles)


def _method_touchtools_reason(
    output_artifact_type: str,
    *,
    touchtools_requested: bool,
    touchtools_titles: list[str],
) -> str:
    if touchtools_requested:
        return (
            "The request/spec mentions TouchTools or touch-monitor operation; "
            "TouchTools launch requires a method, not a standalone script."
        )
    if touchtools_titles:
        return (
            "TouchTools/RUP operator screens were detected in source or generated XML; "
            "prepare a method and enable its TouchTools visibility if operators should start it from the touch monitor."
        )
    if output_artifact_type == "script":
        return "No TouchTools requirement was detected; the generated output can remain a direct-run script after normal validation."
    return "No TouchTools requirement was detected; confirm method settings only if this artifact will be operator-facing."


def _method_touchtools_manual_checks(
    *,
    method_required: bool,
    touchtools_visibility_required: bool,
    startup_variable_count: int,
    operator_prompt_count: int,
) -> list[str]:
    checks = []
    if method_required:
        checks.append("Prepare or create a FluentControl method containing the generated script before TouchTools use.")
    if touchtools_visibility_required:
        checks.append("In the method TouchTools settings, enable `Is visible in Touch Tools` before expecting it in Method Starter.")
    if startup_variable_count:
        checks.append("Review startup variable defaults and prompt text before saving the method.")
    if operator_prompt_count:
        checks.append("Run the method/script in FluentControl simulation to verify each operator prompt appears in the intended order.")
    checks.append("Confirm user-role method permissions and method approval/release status on the target FluentControl system.")
    return checks


def _readiness_startup_variables(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    variables = []
    for record in records:
        source_label = record.get("object_name") or Path(str(record.get("path") or "")).name
        for variable in record.get("startup_variables") or []:
            if not isinstance(variable, dict):
                continue
            if not variable.get("query_on_startup") and not variable.get("prompt"):
                continue
            item = dict(variable)
            item["source"] = str(source_label or "")
            variables.append(item)
    return _dedupe_startup_variables(variables)


def _dedupe_startup_variables(variables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen: set[tuple[str, str, str, str]] = set()
    for variable in variables:
        if not isinstance(variable, dict):
            continue
        key = (
            str(variable.get("source") or ""),
            str(variable.get("name") or ""),
            str(variable.get("scope") or ""),
            str(variable.get("prompt") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(variable)
    return out


def _dedupe_operator_prompts(prompts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen: set[tuple[str, str, str]] = set()
    for prompt in prompts:
        if not isinstance(prompt, dict):
            continue
        key = (
            str(prompt.get("kind") or ""),
            str(prompt.get("title") or ""),
            str(prompt.get("line_number") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(prompt)
    return out


def _render_method_touchtools_readiness(report: dict[str, Any]) -> str:
    decision = report.get("decision") or {}
    summary = report.get("summary") or {}
    output = report.get("output") or {}
    lines = [
        "# Method and TouchTools Readiness",
        "",
        "This report separates direct script readiness from TouchTools/method readiness.",
        "",
        "## Summary",
        "",
        f"- Generated artifact: `{output.get('filename')}`",
        f"- Artifact type: `{output.get('artifact_type')}`",
        f"- Status: `{report.get('status')}`",
        f"- TouchTools workflow detected/requested: `{_yes_no(decision.get('touchtools_workflow_detected'))}`",
        f"- Method required before TouchTools use: `{_yes_no(decision.get('method_required_before_touchtools'))}`",
        f"- TouchTools-visible method setting required: `{_yes_no(decision.get('touchtools_visibility_required'))}`",
        f"- TouchTools/RUP screen titles: `{summary.get('touchtools_title_count', 0)}`",
        f"- Startup variables needing review: `{summary.get('startup_variable_review_count', 0)}`",
        f"- Operator prompt statements: `{summary.get('operator_prompt_count', 0)}`",
        "",
        "## Decision",
        "",
        str(decision.get("reason") or "No decision reason was recorded."),
        "",
    ]

    variables = report.get("startup_variables") or []
    if variables:
        lines.extend(["## Startup Variables To Review", ""])
        for variable in variables[:40]:
            defaults = variable.get("default_values") or []
            default_text = ", ".join(str(value) for value in defaults[:5]) if defaults else ""
            details = [
                f"type `{variable.get('type') or 'unknown'}`",
                f"scope `{variable.get('scope') or 'unknown'}`",
                f"query-on-startup `{_yes_no(variable.get('query_on_startup'))}`",
            ]
            if default_text:
                details.append(f"default `{default_text}`")
            lines.append(f"- `{variable.get('name')}` ({', '.join(details)})")
            if variable.get("prompt"):
                lines.append(f"  - Prompt text: `{variable.get('prompt')}`")
            if variable.get("source"):
                lines.append(f"  - Source: `{variable.get('source')}`")
        if len(variables) > 40:
            lines.append(f"- `{len(variables) - 40}` additional variables are listed in `reports/method_touchtools_readiness.json`.")
        lines.append("")
    else:
        lines.extend(["## Startup Variables To Review", "", "No query-on-startup variables or variable prompt strings were detected.", ""])

    prompts = report.get("operator_prompts") or []
    if prompts:
        lines.extend(["## Operator Prompt Screens", ""])
        for prompt in prompts[:40]:
            title = prompt.get("title") or "<untitled>"
            lines.append(f"- `{title}`")
            lines.append(f"  - Kind: `{prompt.get('kind')}`")
            if prompt.get("instructions"):
                lines.append(f"  - Instructions: `{prompt.get('instructions')}`")
            variables = [
                item
                for item in prompt.get("variables") or []
                if item.get("name") or item.get("display_text")
            ]
            for variable in variables[:6]:
                label = variable.get("name") or variable.get("display_text")
                display = variable.get("display_text") or variable.get("display_type") or ""
                allowed = variable.get("allowed_values") or ""
                suffix = f" - {display}" if display else ""
                lines.append(f"  - Variable `{label}`{suffix}")
                if allowed:
                    lines.append(f"    - Allowed values: `{allowed}`")
            if len(variables) > 6:
                lines.append(f"  - Additional prompt variables: `{len(variables) - 6}`")
        if len(prompts) > 40:
            lines.append(f"- `{len(prompts) - 40}` additional prompts are listed in `reports/method_touchtools_readiness.json`.")
        lines.append("")
    else:
        lines.extend(["## Operator Prompt Screens", "", "No TouchTools/RUP operator prompt screens were detected.", ""])

    titles = report.get("touchtools_titles") or []
    if titles:
        lines.extend(["## TouchTools Titles", ""])
        for title in titles[:40]:
            lines.append(f"- `{title}`")
        if len(titles) > 40:
            lines.append(f"- `{len(titles) - 40}` additional titles are listed in `reports/method_touchtools_readiness.json`.")
        lines.append("")

    checks = report.get("manual_checks") or []
    if checks:
        lines.extend(["## Manual Checks", ""])
        for index, check in enumerate(checks, start=1):
            lines.append(f"{index}. {check}")
        lines.append("")

    lines.append("Machine-readable evidence is in `reports/method_touchtools_readiness.json`.")
    return "\n".join(lines).rstrip() + "\n"


def _yes_no(value: object) -> str:
    return "yes" if bool(value) else "no"


def _write_protocol_ir_from_draft(draft_path: Path, destination: Path) -> None:
    try:
        ir = protocol_ir_from_python(draft_path)
    except Exception as exc:
        _write_unavailable_json(destination, f"failed to derive canonical IR from draft: {exc}")
        return
    write_protocol_ir(ir, destination)


def _write_unavailable_json(destination: Path, reason: str) -> None:
    ensure_parent(destination)
    destination.write_text(
        json.dumps(
            {
                "ir_version": "unavailable",
                "reason": reason,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_recreate_from_ir(
    protocol_ir_path: Path,
    destination: Path,
    *,
    worklist_present: bool = False,
    project_archive_present: bool = False,
    request_spec_present: bool = False,
) -> bool:
    try:
        ir = load_protocol_ir(protocol_ir_path)
    except Exception:
        return False
    generated_files = {
        "ir": "source/protocol.ir.json",
        "python": "source/protocol_draft.py",
        "xscr": "direct-imports/scripts/full-script/generated_script.xscr",
    }
    if request_spec_present:
        generated_files["request_spec"] = "source/request.spec.yaml"
    if worklist_present:
        generated_files["gwl"] = "direct-imports/worklists/generated_worklist.gwl"
    if project_archive_present:
        generated_files["zeia"] = "direct-imports/projects/full-project/generated_project.zeia"
    destination.write_text(
        render_recreate_markdown(
            ir,
            generated_files=generated_files,
        ),
        encoding="utf-8",
    )
    return True


def _write_recreate_unavailable(protocol_ir_path: Path, destination: Path) -> None:
    destination.write_text(
        "\n".join(
            [
                "# Recreate Script: unavailable",
                "",
                "This guide was not generated because the canonical protocol IR could not be loaded.",
                "",
                f"- Source of truth: `{protocol_ir_path.name}`",
                "",
                "Regenerate a valid `protocol.ir.json`, then rebuild the bundle.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_worktable_changes_from_ir(
    protocol_ir_path: Path,
    destination: Path,
    *,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
) -> bool:
    diff = _worktable_diff_from_ir(
        protocol_ir_path,
        source_manifest=source_manifest,
        source_xscr=source_xscr,
        source_scripts=source_scripts,
    )
    if diff is None:
        return False
    destination.write_text(render_worktable_changes_markdown(diff), encoding="utf-8")
    return True


def _write_worktable_patch_from_ir(
    protocol_ir_path: Path,
    destination: Path,
    *,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
) -> bool:
    diff = _worktable_diff_from_ir(
        protocol_ir_path,
        source_manifest=source_manifest,
        source_xscr=source_xscr,
        source_scripts=source_scripts,
    )
    if diff is None:
        return False
    destination.write_text(render_worktable_patch_json(diff), encoding="utf-8")
    return True


def _worktable_diff_from_ir(
    protocol_ir_path: Path,
    *,
    source_manifest: dict[str, Any] | None,
    source_xscr: Path | None,
    source_scripts: list[Path],
) -> dict[str, Any] | None:
    try:
        ir = load_protocol_ir(protocol_ir_path)
    except Exception:
        return None

    source_irs = []
    for source in _dedupe_paths([*source_scripts, *([source_xscr] if source_xscr is not None else [])]):
        if not source.exists() or source.suffix.lower() != ".xscr":
            continue
        try:
            from .protocol_ir import protocol_ir_from_xscr

            source_irs.append(protocol_ir_from_xscr(source))
        except Exception:
            continue

    return diff_worktable_requirements(
        ir,
        source_manifest=source_manifest,
        source_irs=source_irs,
    )


def _render_worktable_changes_unavailable(protocol_ir_path: Path) -> str:
    return (
        "# Worktable Changes\n\n"
        "This report was not generated because the canonical protocol IR could not be loaded.\n\n"
        f"- Source of truth: `{protocol_ir_path.name}`\n\n"
        "Regenerate a valid `protocol.ir.json`, then rebuild the bundle.\n"
    )


def _render_worktable_patch_unavailable(protocol_ir_path: Path) -> str:
    return json.dumps(
        {
            "kind": "worktable_patch",
            "schema_version": "tecan.worktable_patch.v1",
            "status": "unavailable",
            "source_of_truth": protocol_ir_path.name,
            "summary": {
                "operation_count": 0,
                "severity_counts": {"blocking": 1, "needs_review": 0, "safe": 0},
                "overall_severity": "blocking",
                "has_blocking": True,
                "has_needs_review": False,
                "warning_count": 1,
                "manual_step_count": 0,
            },
            "operations": [],
            "warnings": [
                {
                    "id": "warning.1",
                    "severity": "blocking",
                    "message": "Worktable patch was not generated because the canonical protocol IR could not be loaded.",
                }
            ],
            "manual_setup_steps": [],
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    out = []
    seen = set()
    for path in paths:
        if path is None:
            continue
        resolved = Path(path).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _original_source_kind(path: Path) -> str:
    if path.suffix.lower() == ".zeia":
        return "source-project"
    if path.suffix.lower() == ".xscr":
        return "source-script"
    return "original-source"


def _original_source_name(path: Path, kind: str, index: int) -> str:
    suffix = path.suffix or ""
    if kind == "source-project":
        return f"source_project_{index}{suffix or '.zeia'}"
    if kind == "source-script":
        return f"source_script_{index}{suffix or '.xscr'}"
    return f"source_file_{index}{suffix}"


def _strict_report_map(report_files: dict[str, Path], reports: list[Path]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    supporting = []
    for key, path in report_files.items():
        normalized = _normalize_report_key(key)
        if normalized in STRICT_REPORT_FILENAMES:
            mapped[normalized] = path
        else:
            supporting.append(path)
    for report in reports:
        key = _classify_report(report)
        if key and key not in mapped:
            mapped[key] = report
        else:
            supporting.append(report)
    mapped["supporting_reports"] = supporting
    return mapped


def _normalize_report_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _classify_report(path: Path) -> str | None:
    name = path.name.lower()
    if "project_report" in name or name == "project.md":
        return "project_report"
    if "simulation" in name:
        return "simulation_report"
    if "repair_history" in name:
        return "repair_history"
    if "repair" in name:
        return "repair_plan"
    if "compile" in name:
        return "compile_report"
    return None


def _placeholder_report(key: str) -> str:
    titles = {
        "project_report": "Project Report",
        "simulation_report": "Simulation Report",
        "repair_plan": "Repair Plan",
        "repair_history": "Repair History",
        "compile_report": "Compile Report",
    }
    return (
        f"# {titles.get(key, key.replace('_', ' ').title())}\n\n"
        "This report was not produced for this bundle.\n"
    )


def _render_worktable_changes(details: dict[str, object]) -> str:
    lines = ["# Worktable Changes", ""]
    worktable = details.get("worktable")
    if isinstance(worktable, dict) and worktable:
        lines.extend(["## Worktable Used", ""])
        for label, key in (
            ("Base worktable", "base_workspace"),
            ("Worktable GUID", "workspace_guid"),
            ("Protocol name", "protocol_name"),
            ("Comment", "comment"),
            ("Auto-place labware", "auto_place"),
        ):
            value = worktable.get(key)
            if _has_value(value):
                lines.append(f"- {label}: `{value}`")
        lines.append("")
    else:
        lines.extend(["No worktable metadata was detected in the exported artifacts.", ""])

    selected_items = details.get("selected_items")
    labware_items = [
        item for item in selected_items
        if isinstance(item, dict) and str(item.get("kind") or "") == "labware"
    ] if isinstance(selected_items, list) else []
    if labware_items:
        lines.extend(["## Labware Placements", ""])
        for item in labware_items:
            lines.append(f"- `{item.get('label') or item.get('name') or item.get('catalog')}`")
            for label, key in (
                ("Catalog / FluentControl type", "catalog"),
                ("Python class", "python_class"),
                ("Deck location", "deck_location"),
                ("Initial contents", "initial_contents"),
                ("Source path", "source_path"),
            ):
                value = item.get(key)
                if _has_value(value):
                    lines.append(f"  - {label}: `{value}`")
        lines.append("")
    else:
        lines.extend(
            [
                "## Labware Placements",
                "",
                "No generated labware placement changes were detected.",
                "",
            ]
        )

    lines.extend(
        [
            "## Review Notes",
            "",
            "- Confirm the base worktable, deck sites, carriers, and labware definitions in FluentControl.",
            "- Treat this file as a review aid; it is not an instrument validation.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_recreate_guide(metadata: dict[str, object]) -> str:
    files = metadata.get("files") or []
    compiled = str(metadata.get("compiled_xscr") or "")
    draft = _first_file(files, "protocol-draft") or _first_file(files, "source-draft")
    original = _first_file(files, "source-script") or _first_file(files, "original-script")
    reports = [
        item for item in files
        if isinstance(item, dict)
        and (
            str(item.get("kind") or "").endswith("_report")
            or item.get("kind") in {"project_report", "simulation_report", "repair_plan", "compile_report"}
        )
    ]
    context = metadata.get("context_name") or "none"

    lines = [
        "# Recreate Script Guide",
        "",
        f"- Script: `{metadata.get('script_name')}`",
        f"- Context: `{context}`",
        f"- Compiled draft: `{compiled}`",
        "",
        "## Import The Compiled Draft",
        "",
        f"Use `{compiled}` as the compiled `.xscr` draft to manually import or load in FluentControl.",
        "Review and validate it in FluentControl before any real run.",
        "",
        "## Recreate Instead Of Importing",
        "",
    ]
    if draft:
        lines.extend(
            [
                f"1. Review the Python draft: `{draft}`",
                "2. From `03-protocol-builder`, simulate the draft again:",
                "",
                "```powershell",
                _simulate_command(draft, context),
                "```",
                "",
                "3. If simulation is acceptable, compile it again:",
                "",
                "```powershell",
                _compile_command(draft, compiled, context),
                "```",
                "",
            ]
        )
    elif original:
        lines.extend(
            [
                f"1. Start from the original script copy: `{original}`",
                "2. From `03-protocol-builder`, decompile or roundtrip it again:",
                "",
                "```powershell",
                _roundtrip_command(original, context),
                "```",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "No source draft or original `.xscr` was exported with this compiled script.",
                "Use the reports in this folder to recreate the workflow manually in FluentControl.",
                "",
            ]
        )

    manual_recreation = metadata.get("manual_recreation")
    if isinstance(manual_recreation, dict) and manual_recreation:
        lines.extend(_render_manual_recreation(manual_recreation))

    if reports:
        lines.extend(["## Supporting Files", ""])
        for item in reports:
            lines.append(f"- `{item.get('relative_path') or item.get('filename')}`")
        lines.append("")

    lines.extend(
        [
            "## Manual Recreation Checklist",
            "",
            "1. Confirm the project/worktable context.",
            "2. Confirm labware names, deck sites, liquid classes, and tip types.",
            "3. Recreate commands in FluentControl using the reports as references.",
            "4. Validate the script in FluentControl before any instrument use.",
            "",
            "This folder is a handoff package, not an approval signal.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _first_file(files: object, kind: str) -> str | None:
    if not isinstance(files, list):
        return None
    for item in files:
        if isinstance(item, dict) and item.get("kind") == kind:
            return str(item.get("relative_path") or item.get("filename") or "")
    return None


def _manual_recreation_details(
    compiled_xscr: Path,
    *,
    draft_path: Path | None,
    source_xscr: Path | None,
) -> dict[str, object]:
    xscr_details = _extract_xscr_manual_details(compiled_xscr)
    if not xscr_details and source_xscr is not None:
        xscr_details = _extract_xscr_manual_details(source_xscr)
    draft_details = _extract_draft_manual_details(draft_path)

    worktable = _merge_nonempty_dicts(
        xscr_details.get("worktable"),
        draft_details.get("worktable"),
    )
    selected_items = _merge_selected_items(
        xscr_details.get("selected_items"),
        draft_details.get("selected_items"),
    )
    commands = _merge_commands(
        xscr_details.get("commands"),
        draft_details.get("commands"),
    )

    details: dict[str, object] = {}
    if worktable:
        details["worktable"] = worktable
    if selected_items:
        details["selected_items"] = selected_items
    if commands:
        details["commands"] = commands
    return details


def _extract_draft_manual_details(draft_path: Path | None) -> dict[str, object]:
    if draft_path is None or not draft_path.exists():
        return {}
    try:
        source = draft_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}

    build = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "build_worktable"
        ),
        None,
    )
    if build is None:
        return {}

    worktable: dict[str, object] = {}
    selected_items: list[dict[str, object]] = []
    commands: list[dict[str, object]] = []
    reagents_by_var: dict[str, str] = {}
    labware_by_var: dict[str, dict[str, object]] = {}
    current_group = "Ungrouped"

    for statement in build.body:
        target_name = _assignment_target_name(statement)
        value = statement.value if isinstance(statement, (ast.Assign, ast.Expr)) else statement
        call = value if isinstance(value, ast.Call) else None

        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Attribute):
            if statement.value.attr.lower() in {"mca96", "liha", "fca"} and target_name:
                selected_items.append(
                    {
                        "kind": "device",
                        "name": _friendly_device_name(statement.value.attr),
                        "source_path": _source_path(draft_path.name, current_group, source, statement.value),
                    }
                )
            continue

        if call is None:
            continue

        if _is_worktable_from_workspace(call):
            worktable.update(_draft_worktable_details(call))
            continue

        if _call_name(call) == "Reagent" and target_name:
            reagent_name = _value_label(call.args[0], reagents_by_var, labware_by_var) if call.args else target_name
            reagents_by_var[target_name] = reagent_name
            selected_items.append(
                {
                    "kind": "reagent",
                    "name": reagent_name,
                    "source_path": _source_path(draft_path.name, current_group, source, call),
                }
            )
            continue

        if _is_method_call(call, "group"):
            current_group = _value_label(call.args[0], reagents_by_var, labware_by_var) if call.args else current_group
            continue

        if _is_method_call(call, "place"):
            item = _draft_labware_item(call, draft_path.name, current_group, source)
            if item:
                selected_items.append(item)
                if target_name:
                    labware_by_var[target_name] = item
                commands.append(_draft_add_labware_command(item, draft_path.name, current_group, source, call))
            continue

        if _is_fill_all_call(call):
            _apply_fill_all(call, labware_by_var, reagents_by_var)
            continue

        command = _draft_runtime_command(call, draft_path.name, current_group, source, reagents_by_var, labware_by_var)
        if command:
            commands.append(command)
            for item in _draft_command_selected_items(call, draft_path.name, current_group, source):
                selected_items.append(item)

    return {
        "worktable": worktable,
        "selected_items": selected_items,
        "commands": commands,
    }


def _extract_xscr_manual_details(xscr_path: Path | None) -> dict[str, object]:
    if xscr_path is None or not xscr_path.exists():
        return {}
    try:
        root = ET.parse(xscr_path).getroot()
    except (ET.ParseError, OSError):
        return {}

    worktable: dict[str, object] = {}
    selected_items: list[dict[str, object]] = []
    commands: list[dict[str, object]] = []

    payload = _first_descendant(root, "Payload")
    if payload is not None:
        protocol_name = _direct_text(payload, "ObjectName")
        comment = _direct_text(payload, "Comment")
        if protocol_name:
            worktable["protocol_name"] = protocol_name
        if comment:
            worktable["comment"] = comment

    for reference in _iter_local(root, "Reference"):
        ref_type = _direct_text(reference, "TypeId")
        ref_name = _direct_text(reference, "ObjectName")
        ref_guid = _direct_text(reference, "Guid")
        if ref_type == "WorktableWorkspace":
            if ref_name:
                worktable["base_workspace"] = ref_name
            if ref_guid:
                worktable["workspace_guid"] = ref_guid
        elif ref_type == "LiquidClass" and ref_name:
            selected_items.append(
                {
                    "kind": "liquid_class",
                    "name": ref_name,
                    "guid": ref_guid,
                    "path": "References -> LiquidClass",
                }
            )

    for group_object in _iter_local(root, "Object"):
        object_type = str(group_object.attrib.get("Type") or "")
        if not object_type.endswith("ScriptGroupDataV1"):
            continue
        group_data = _direct_child(group_object, "ScriptGroupDataV1")
        group_name = _direct_text(group_data, "Name") if group_data is not None else None
        statements = _first_descendant(group_object, "Statements")
        if statements is None:
            continue
        for command_object in _direct_children(statements, "Object"):
            command = _xscr_command_details(command_object, group_name or "Ungrouped")
            if not command:
                continue
            commands.append(command)
            item = _selected_item_from_xscr_command(command)
            if item:
                selected_items.append(item)

    return {
        "worktable": worktable,
        "selected_items": selected_items,
        "commands": commands,
    }


def _render_manual_recreation(details: dict[str, object]) -> list[str]:
    lines = ["## Manual Recreation Details", ""]

    worktable = details.get("worktable")
    if isinstance(worktable, dict) and worktable:
        lines.extend(["### Worktable Used", ""])
        for label, key in (
            ("Base worktable", "base_workspace"),
            ("Worktable GUID", "workspace_guid"),
            ("Protocol name", "protocol_name"),
            ("Comment", "comment"),
            ("Auto-place labware", "auto_place"),
        ):
            value = worktable.get(key)
            if _has_value(value):
                lines.append(f"- {label}: `{value}`")
        lines.append("")

    selected_items = details.get("selected_items")
    if isinstance(selected_items, list) and selected_items:
        lines.extend(["### Chosen Items", ""])
        for item in selected_items:
            if not isinstance(item, dict):
                continue
            name = item.get("label") or item.get("name") or item.get("catalog") or item.get("kind")
            lines.append(f"- `{name}`")
            for field_label, key in (
                ("Item kind", "kind"),
                ("Python class", "python_class"),
                ("Catalog / FluentControl type", "catalog"),
                ("Deck location", "deck_location"),
                ("Initial contents", "initial_contents"),
                ("GUID", "guid"),
                ("Path", "path"),
                ("Source path", "source_path"),
            ):
                value = item.get(key)
                if _has_value(value):
                    lines.append(f"  - {field_label}: `{value}`")
        lines.append("")

    commands = details.get("commands")
    if isinstance(commands, list) and commands:
        lines.extend(["### Command List", ""])
        for index, command in enumerate(commands, start=1):
            if not isinstance(command, dict):
                continue
            name = str(command.get("name") or command.get("command_id") or "Command")
            lines.append(f"{index}. {name}")
            lines.append(f"   - Command name: `{name}`")
            if _has_value(command.get("command_id")):
                lines.append(f"   - FluentControl command ID: `{command.get('command_id')}`")
            specs = command.get("specifications")
            if isinstance(specs, list) and specs:
                lines.append("   - Specifications:")
                for spec in specs:
                    lines.append(f"     - {spec}")
            path = command.get("path")
            source_path = command.get("source_path")
            if _has_value(path) or _has_value(source_path):
                lines.append("   - Path to find it:")
                if _has_value(path):
                    lines.append(f"     - Compiled script: `{path}`")
                if _has_value(source_path):
                    lines.append(f"     - Source draft: `{source_path}`")
        lines.append("")

    return lines


def _merge_nonempty_dicts(*values: object) -> dict[str, object]:
    merged: dict[str, object] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if _has_value(item):
                merged[str(key)] = item
    return merged


def _merge_selected_items(*values: object) -> list[dict[str, object]]:
    merged: dict[tuple[str, str], dict[str, object]] = {}
    order: list[tuple[str, str]] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            key = _selected_item_key(item)
            if key not in merged:
                merged[key] = {}
                order.append(key)
            merged[key].update({str(k): v for k, v in item.items() if _has_value(v)})
    return [merged[key] for key in order]


def _merge_commands(xscr_commands: object, draft_commands: object) -> list[dict[str, object]]:
    if isinstance(xscr_commands, list) and xscr_commands:
        commands = [dict(command) for command in xscr_commands if isinstance(command, dict)]
        if isinstance(draft_commands, list):
            draft_dicts = [command for command in draft_commands if isinstance(command, dict)]
            for command, draft_command in zip(commands, draft_dicts):
                if _has_value(draft_command.get("source_path")):
                    command["source_path"] = draft_command["source_path"]
        return commands
    if isinstance(draft_commands, list):
        return [dict(command) for command in draft_commands if isinstance(command, dict)]
    return []


def _selected_item_key(item: dict[str, object]) -> tuple[str, str]:
    kind = str(item.get("kind") or "")
    name = str(item.get("label") or item.get("name") or item.get("catalog") or item.get("path") or "")
    return kind, name


def _has_value(value: object) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _assignment_target_name(statement: ast.stmt) -> str | None:
    if not isinstance(statement, ast.Assign) or not statement.targets:
        return None
    target = statement.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _is_worktable_from_workspace(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "from_workspace"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "Worktable"
    )


def _draft_worktable_details(call: ast.Call) -> dict[str, object]:
    details: dict[str, object] = {}
    if call.args:
        details["base_workspace"] = _literal_text(call.args[0])
    for key in ("workspace_guid", "auto_place", "protocol_name", "comment"):
        value = _keyword_value(call, key)
        if _has_value(value):
            details[key] = value
    return details


def _draft_labware_item(
    call: ast.Call,
    draft_name: str,
    current_group: str,
    source: str,
) -> dict[str, object] | None:
    if not call.args or not isinstance(call.args[0], ast.Call):
        return None
    labware_call = call.args[0]
    label = _literal_text(labware_call.args[0]) if labware_call.args else None
    catalog = _keyword_value(labware_call, "catalog")
    location = _literal_text(call.args[1]) if len(call.args) > 1 else None
    position = _literal_text(call.args[2]) if len(call.args) > 2 else None
    item: dict[str, object] = {
        "kind": "labware",
        "label": label,
        "python_class": _call_name(labware_call),
        "catalog": catalog,
        "source_path": _source_path(draft_name, current_group, source, call),
    }
    if _has_value(location) and _has_value(position):
        item["deck_location"] = f"{location} {position}"
    elif _has_value(location):
        item["deck_location"] = location
    return {key: value for key, value in item.items() if _has_value(value)}


def _draft_add_labware_command(
    item: dict[str, object],
    draft_name: str,
    current_group: str,
    source: str,
    call: ast.Call,
) -> dict[str, object]:
    specs = []
    for label, key in (
        ("Labware label", "label"),
        ("Python class", "python_class"),
        ("Catalog / FluentControl type", "catalog"),
        ("Deck location", "deck_location"),
    ):
        if _has_value(item.get(key)):
            specs.append(f"{label}: `{item[key]}`")
    return {
        "name": "Add Labware",
        "command_id": "wt.place",
        "group": current_group,
        "specifications": specs,
        "source_path": _source_path(draft_name, current_group, source, call),
    }


def _apply_fill_all(
    call: ast.Call,
    labware_by_var: dict[str, dict[str, object]],
    reagents_by_var: dict[str, str],
) -> None:
    target = call.func.value
    if not isinstance(target, ast.Name) or target.id not in labware_by_var:
        return
    reagent = _value_label(call.args[0], reagents_by_var, labware_by_var) if call.args else None
    volume = _literal_text(call.args[1]) if len(call.args) > 1 else None
    if _has_value(reagent) and _has_value(volume):
        labware_by_var[target.id]["initial_contents"] = f"{reagent}, {volume} uL in all wells"


def _draft_runtime_command(
    call: ast.Call,
    draft_name: str,
    current_group: str,
    source: str,
    reagents_by_var: dict[str, str],
    labware_by_var: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    name = _PYTHON_COMMAND_NAMES.get(call.func.attr)
    if not name:
        return None

    specs: list[str] = []
    if call.args:
        target = _value_label(call.args[0], reagents_by_var, labware_by_var)
        if _has_value(target):
            specs.append(f"Target labware: `{target}`")
    if call.func.attr in {"aspirate", "dispense"} and len(call.args) > 1:
        volume = _literal_text(call.args[1])
        if _has_value(volume):
            specs.append(f"Volume: `{volume} uL`")
    liquid_class = _keyword_value(call, "liquid_class")
    if _has_value(liquid_class):
        specs.append(f"Liquid class: `{liquid_class}`")

    return {
        "name": name,
        "command_id": call.func.attr,
        "group": current_group,
        "specifications": specs,
        "source_path": _source_path(draft_name, current_group, source, call),
    }


def _draft_command_selected_items(
    call: ast.Call,
    draft_name: str,
    current_group: str,
    source: str,
) -> list[dict[str, object]]:
    liquid_class = _keyword_value(call, "liquid_class")
    if not _has_value(liquid_class):
        return []
    return [
        {
            "kind": "liquid_class",
            "name": liquid_class,
            "source_path": _source_path(draft_name, current_group, source, call),
        }
    ]


def _is_method_call(call: ast.Call, method_name: str) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == method_name


def _is_fill_all_call(call: ast.Call) -> bool:
    return _is_method_call(call, "fill_all")


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _keyword_value(call: ast.Call, key: str) -> object:
    for keyword in call.keywords:
        if keyword.arg == key:
            return _literal_text(keyword.value)
    return None


def _value_label(
    node: ast.AST,
    reagents_by_var: dict[str, str],
    labware_by_var: dict[str, dict[str, object]],
) -> str:
    literal = _literal_text(node)
    if _has_value(literal):
        return str(literal)
    if isinstance(node, ast.Name):
        if node.id in labware_by_var:
            return str(labware_by_var[node.id].get("label") or node.id)
        return reagents_by_var.get(node.id, node.id)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "labware_by_label" and node.args:
            return str(_literal_text(node.args[0]) or "")
    if isinstance(node, ast.Attribute):
        return node.attr
    return ast.unparse(node) if hasattr(ast, "unparse") else ""


def _literal_text(node: ast.AST) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _source_path(draft_name: str, current_group: str, source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node) or ""
    segment = " ".join(segment.split())
    if len(segment) > 120:
        segment = segment[:117].rstrip() + "..."
    return f"{draft_name} -> build_worktable() -> {current_group} -> {segment}"


def _friendly_device_name(value: str) -> str:
    names = {
        "mca96": "MCA96 head",
        "liha": "LiHa arm",
        "fca": "FCA arm",
    }
    return names.get(value.lower(), value)


def _xscr_command_details(command_object: ET.Element, group_name: str) -> dict[str, object] | None:
    command_id = _command_id(command_object)
    if not command_id:
        return None
    line_number = _first_text(command_object, "LineNumber")
    path = f"Commands -> {group_name}"
    if line_number:
        path += f" -> Line {line_number}"
    path += f" -> {command_id}"
    return {
        "name": _friendly_command_name(command_id),
        "command_id": command_id,
        "group": group_name,
        "line_number": line_number,
        "path": path,
        "specifications": _xscr_command_specs(command_object, command_id),
    }


def _selected_item_from_xscr_command(command: dict[str, object]) -> dict[str, object] | None:
    if command.get("command_id") != "AddLabwareDataV1":
        return None
    item: dict[str, object] = {"kind": "labware", "path": command.get("path")}
    for spec in command.get("specifications") or []:
        if not isinstance(spec, str) or ": `" not in spec:
            continue
        label, value = spec.split(": `", 1)
        value = value.rstrip("`")
        if label == "Labware label":
            item["label"] = value
        elif label == "Labware type":
            item["catalog"] = value
        elif label == "Deck location":
            item["deck_location"] = value
    return item if _has_value(item.get("label")) else None


def _xscr_command_specs(command_object: ET.Element, command_id: str) -> list[str]:
    specs: list[str] = []
    if command_id == "AddLabwareDataV1":
        _append_spec(specs, "Labware label", _first_text(command_object, "LabwareLable"))
        _append_spec(specs, "Labware type", _first_text(command_object, "LabwareType"))
        location = _first_text(command_object, "Location")
        position = _first_text(command_object, "Position")
        if location and position:
            _append_spec(specs, "Deck location", f"{location} {position}")
        else:
            _append_spec(specs, "Location", location)
            _append_spec(specs, "Position", position)
        _append_spec(specs, "Rotation", _first_text(command_object, "Rotation"))
        _append_spec(specs, "Has lid", _first_text(command_object, "HasLid"))
        return specs

    _append_spec(specs, "Labware", _first_text(command_object, "LabwareName"))
    _append_spec(specs, "Volume", _with_unit(_first_text(command_object, "Volume"), "uL"))
    _append_spec(
        specs,
        "Liquid class",
        _first_nonempty_text(command_object, ["LiquidClassNameBySelection", "LiquidClassName"]),
    )
    _append_spec(specs, "Device alias", _first_text(command_object, "DeviceAlias"))
    _append_spec(specs, "Device ID", _first_text(command_object, "AvailableID"))
    _append_spec(specs, "Blowout airgap", _first_text(command_object, "BlowoutAirgap"))
    _append_spec(specs, "Head position", _first_text(command_object, "HeadPositions"))
    _append_spec(specs, "Partial columns", _first_text(command_object, "PartialColumns"))
    _append_spec(specs, "Partial rows", _first_text(command_object, "PartialRows"))
    _append_spec(specs, "Well offset", _first_text(command_object, "WellOffset"))
    _append_spec(specs, "Tip range", _tip_range(command_object))
    _append_spec(specs, "Usable tips", _first_text(command_object, "UsableTips"))
    _append_spec(specs, "Adapter", _adapter_summary(command_object))
    _append_spec(specs, "Back position", _first_text(command_object, "Backs"))
    _append_spec(specs, "Remove rack", _first_text(command_object, "RemoveRack"))
    _append_spec(specs, "Adapter after drop", _first_text(command_object, "AdapterAfterDrop"))
    return specs


def _append_spec(specs: list[str], label: str, value: object) -> None:
    if _has_value(value):
        specs.append(f"{label}: `{value}`")


def _with_unit(value: object, unit: str) -> str | None:
    if not _has_value(value):
        return None
    return f"{value} {unit}"


def _tip_range(command_object: ET.Element) -> str | None:
    first_x = _first_text(command_object, "FirstTipXPosition")
    last_x = _first_text(command_object, "LastTipXPosition")
    first_y = _first_text(command_object, "FirstTipYPosition")
    last_y = _first_text(command_object, "LastTipYPosition")
    if all([first_x, last_x, first_y, last_y]):
        return f"X {first_x}-{last_x}, Y {first_y}-{last_y}"
    return None


def _adapter_summary(command_object: ET.Element) -> str | None:
    adapter = _first_descendant(command_object, "AdapterData")
    if adapter is None:
        return None
    name = _direct_text(adapter, "Name")
    adapter_type = _direct_text(adapter, "Type")
    adapter_id = _direct_text(adapter, "ID")
    parts = [part for part in (name, adapter_type, adapter_id) if part]
    return " / ".join(parts) if parts else None


def _command_id(command_object: ET.Element) -> str:
    for child in list(command_object):
        return _local_name(child.tag)
    object_type = str(command_object.attrib.get("Type") or "")
    return object_type.rsplit(".", 1)[-1]


def _friendly_command_name(command_id: str) -> str:
    for key, name in _XSCR_COMMAND_NAMES.items():
        if key in command_id:
            return name
    cleaned = re.sub(r"(ScriptCommand)?DataV\d+$", "", command_id)
    cleaned = re.sub(r"^Mca\d+", "MCA ", cleaned)
    return re.sub(r"(?<!^)([A-Z])", r" \1", cleaned).strip()


def _first_nonempty_text(parent: ET.Element, names: list[str]) -> str | None:
    for name in names:
        value = _first_text(parent, name)
        if value:
            return value
    return None


def _first_text(parent: ET.Element, name: str) -> str | None:
    node = _first_descendant(parent, name)
    return _text(node)


def _direct_text(parent: ET.Element | None, name: str) -> str | None:
    child = _direct_child(parent, name)
    return _text(child)


def _text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _first_descendant(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in parent.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_children(parent: ET.Element | None, name: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [child for child in list(parent) if _local_name(child.tag) == name]


def _iter_local(parent: ET.Element, name: str):
    for child in parent.iter():
        if _local_name(child.tag) == name:
            yield child


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_PYTHON_COMMAND_NAMES = {
    "mount_adapter": "Mount Head Adapter",
    "pick_up": "Pick Up Tips",
    "aspirate": "Aspirate",
    "dispense": "Dispense",
    "return_tips": "Return Tips",
    "drop_tips": "Drop Tips",
    "drop_adapter": "Drop Head Adapter",
}


_XSCR_COMMAND_NAMES = {
    "AddLabware": "Add Labware",
    "GetHeadAdapter": "Mount Head Adapter",
    "PickUpTips": "Pick Up Tips",
    "Aspirate": "Aspirate",
    "Dispense": "Dispense",
    "SetTipsBack": "Return Tips",
    "DropTips": "Drop Tips",
    "DropHeadAdapter": "Drop Head Adapter",
}


def _simulate_command(draft: str, context: object) -> str:
    context_part = f" --context {context}" if context and context != "none" else ""
    return (
        f".\\.venv\\Scripts\\python.exe -m fluent_pipeline.cli simulate \"<path-to-this-folder>\\{draft}\""
        f"{context_part} --report temp_files\\recreated_simulation.md --json-out temp_files\\recreated_simulation.json"
    )


def _compile_command(draft: str, compiled: str, context: object) -> str:
    context_part = f" --context {context}" if context and context != "none" else ""
    return (
        f".\\.venv\\Scripts\\python.exe -m fluent_pipeline.cli compile \"<path-to-this-folder>\\{draft}\""
        f"{context_part} -o temp_files\\{compiled}"
    )


def _roundtrip_command(original: str, context: object) -> str:
    context_part = f" --context {context}" if context and context != "none" else ""
    return (
        f".\\.venv\\Scripts\\python.exe -m fluent_pipeline.cli roundtrip "
        f"\"<path-to-this-folder>\\{original}\"{context_part}"
    )
