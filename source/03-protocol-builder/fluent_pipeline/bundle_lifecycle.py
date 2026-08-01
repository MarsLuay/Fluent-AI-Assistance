"""Classify generated bundles and plan safe archive cleanup actions."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import READY_TO_IMPORT_DIR, TEMP_FILES_DIRNAME
from .runner import ensure_parent


LIFECYCLE_SCHEMA_VERSION = "tecan.bundle_lifecycle.v1"
DEFAULT_PROBE_ROOT = READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "build" / "fluent_import_probe"


@dataclass(frozen=True)
class BundleRecord:
    path: Path
    name: str
    bundle_role: str
    source_export_kind: str
    verification_state: str
    created_at: str | None
    context_name: str | None
    script_name: str | None
    supersedes: str | None
    superseded_by: str | None
    metadata_path: Path | None
    generation_manifest_path: Path | None
    recommendation: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "name": self.name,
            "bundle_role": self.bundle_role,
            "source_export_kind": self.source_export_kind,
            "verification_state": self.verification_state,
            "created_at": self.created_at,
            "context_name": self.context_name,
            "script_name": self.script_name,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "metadata_path": str(self.metadata_path) if self.metadata_path else None,
            "generation_manifest_path": str(self.generation_manifest_path) if self.generation_manifest_path else None,
            "recommendation": self.recommendation,
            "reason": self.reason,
        }


def lifecycle_metadata(
    *,
    bundle_role: str,
    source_export_kind: str,
    verification_state: str,
    created_from: dict[str, Any] | None = None,
    supersedes: str | None = None,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    """Return normalized lifecycle fields for metadata/manifests."""
    return {
        "schema_version": LIFECYCLE_SCHEMA_VERSION,
        "bundle_role": bundle_role,
        "source_export_kind": source_export_kind,
        "verification_state": verification_state,
        "created_from": created_from or {},
        "supersedes": supersedes,
        "superseded_by": superseded_by,
    }


def source_export_kind(full_zeia_export: dict[str, Any] | None, *, approved_partial: bool = False) -> str:
    assessment = full_zeia_export or {}
    if assessment.get("accepted") or assessment.get("status") == "likely_full_export":
        return "full"
    if approved_partial or assessment.get("approved_partial_zeia"):
        return "approved_partial"
    if assessment:
        return "partial"
    return "unknown"


def verification_state_from_readiness(
    *,
    ready_to_import: bool,
    readiness: dict[str, Any] | None = None,
    workflow_status: str | None = None,
) -> str:
    if not ready_to_import:
        return "not_validated" if workflow_status == "scaffold_not_validated" else "failed_or_blocked"
    profile = readiness or {}
    if not profile:
        return "offline_validated"
    load_state = (profile.get("fluentcontrol_load_diagnostic") or {}).get("status")
    if not load_state:
        load_state = (profile.get("script_editor_load") or {}).get("status")
    if load_state == "load_clean":
        return "load_tested"
    offline_state = (profile.get("offline_validation") or {}).get("status")
    review_state = (profile.get("review_state") or {}).get("status")
    import_state = (profile.get("generated_zeia_import") or {}).get("status")
    if offline_state == "ready_to_import" or review_state in {
        "import_ready_needs_review",
        "hardware_review_required",
    } or import_state in {
        "ready_to_import",
        "import_ready_needs_review",
    }:
        return "offline_validated"
    return "hardware_pending"


def bundle_role_from_manifest(manifest: dict[str, Any] | None, *, path: Path | None = None) -> str:
    name = (path.name if path else "").casefold()
    if "probe" in name:
        return "probe"
    if not manifest:
        return "debug"
    if manifest.get("bundle_role"):
        return str(manifest["bundle_role"])
    if manifest.get("ready_to_import"):
        return "ready"
    return "debug"


def created_from_record(
    *,
    context_name: str | None = None,
    context_kind: str | None = None,
    source_contexts: list[dict[str, Any]] | None = None,
    source_projects: list[Path] | None = None,
) -> dict[str, Any]:
    return {
        "context_name": context_name,
        "context_kind": context_kind,
        "source_contexts": source_contexts or [],
        "source_projects": [str(path) for path in (source_projects or [])],
    }


def scan_bundle_lifecycle(
    *,
    ready_root: Path = READY_TO_IMPORT_DIR,
    probe_roots: list[Path] | None = None,
    keep_latest_ready: int = 1,
) -> list[BundleRecord]:
    records: list[BundleRecord] = []
    if ready_root.exists():
        for path in sorted(item for item in ready_root.iterdir() if item.is_dir() and item.name != "archive"):
            records.append(_record_ready_bundle(path))
    for probe_root in probe_roots or []:
        if not probe_root.exists():
            continue
        for path in sorted(probe_root.iterdir()):
            if path.name == "archive":
                continue
            records.append(_record_probe_artifact(path))
    return _apply_recommendations(records, keep_latest_ready=keep_latest_ready)


def archive_recommended_bundles(
    records: list[BundleRecord],
    *,
    archive_root: Path,
) -> list[dict[str, str]]:
    moved: list[dict[str, str]] = []
    batch = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for record in records:
        if record.recommendation != "archive":
            continue
        destination = _unique_archive_destination(archive_root / batch / record.name)
        ensure_parent(destination)
        shutil.move(str(record.path), str(destination))
        _mark_archived(destination)
        moved.append({"source": str(record.path), "destination": str(destination), "reason": record.reason})
    return moved


def render_bundle_index(records: list[BundleRecord], *, archived: list[dict[str, str]] | None = None) -> str:
    lines = [
        "# Bundle Index",
        "",
        "This report is safe-by-default lifecycle guidance. Dry runs do not delete or move bundles.",
        "",
        "## Bundles",
        "",
    ]
    if not records:
        lines.append("- No bundles found.")
    for record in records:
        lines.append(f"- `{record.name}`")
        lines.append(f"  - Role: `{record.bundle_role}`")
        lines.append(f"  - Source export: `{record.source_export_kind}`")
        lines.append(f"  - Verification: `{record.verification_state}`")
        lines.append(f"  - Recommendation: `{record.recommendation}` - {record.reason}")
        lines.append(f"  - Path: `{record.path}`")
    if archived is not None:
        lines.extend(["", "## Archive Actions", ""])
        if not archived:
            lines.append("- No bundles were moved.")
        for item in archived:
            lines.append(f"- Moved `{item['source']}` to `{item['destination']}`")
    lines.append("")
    return "\n".join(lines)


def _record_ready_bundle(path: Path) -> BundleRecord:
    metadata_path = path / "source" / "metadata.json"
    manifest_path = path / "source" / "generation_manifest.json"
    metadata = _read_json(metadata_path)
    manifest = _read_json(manifest_path)
    lifecycle = metadata.get("lifecycle") if isinstance(metadata.get("lifecycle"), dict) else {}
    created_at = metadata.get("exported_at") or manifest.get("generated_at") or _mtime_iso(path)
    source_kind = lifecycle.get("source_export_kind") or metadata.get("source_export_kind") or source_export_kind(
        manifest.get("full_zeia_export"),
        approved_partial=bool(manifest.get("partial_zeia_export_approved")),
    )
    ready_to_import = manifest.get("ready_to_import")
    if ready_to_import is None:
        ready_to_import = metadata.get("ready_to_import")
    verification = lifecycle.get("verification_state") or metadata.get("verification_state") or verification_state_from_readiness(
        ready_to_import=bool(ready_to_import),
        readiness=manifest.get("readiness") if isinstance(manifest.get("readiness"), dict) else None,
        workflow_status=manifest.get("workflow_status"),
    )
    return BundleRecord(
        path=path,
        name=path.name,
        bundle_role=lifecycle.get("bundle_role") or metadata.get("bundle_role") or bundle_role_from_manifest(manifest, path=path),
        source_export_kind=str(source_kind),
        verification_state=str(verification),
        created_at=str(created_at) if created_at else None,
        context_name=metadata.get("context_name") or manifest.get("context"),
        script_name=metadata.get("script_name") or path.name,
        supersedes=lifecycle.get("supersedes") or metadata.get("supersedes"),
        superseded_by=lifecycle.get("superseded_by") or metadata.get("superseded_by"),
        metadata_path=metadata_path if metadata_path.exists() else None,
        generation_manifest_path=manifest_path if manifest_path.exists() else None,
        recommendation="keep",
        reason="current candidate until compared with related bundles",
    )


def _record_probe_artifact(path: Path) -> BundleRecord:
    return BundleRecord(
        path=path,
        name=path.name,
        bundle_role="probe",
        source_export_kind="unknown",
        verification_state="probe_only",
        created_at=_mtime_iso(path),
        context_name=None,
        script_name=path.stem,
        supersedes=None,
        superseded_by=None,
        metadata_path=None,
        generation_manifest_path=None,
        recommendation="keep",
        reason="probe artifact until cleanup recommendations are applied",
    )


def _apply_recommendations(records: list[BundleRecord], *, keep_latest_ready: int) -> list[BundleRecord]:
    ready_groups: dict[tuple[str | None, str | None], list[BundleRecord]] = {}
    for record in records:
        if record.bundle_role == "ready":
            ready_groups.setdefault((record.context_name, record.script_name), []).append(record)

    keep_paths: set[Path] = set()
    superseded: dict[Path, str] = {}
    for group in ready_groups.values():
        ordered = sorted(group, key=_created_sort_key, reverse=True)
        keep_paths.update(record.path for record in ordered[:keep_latest_ready])
        for record in ordered[keep_latest_ready:]:
            superseded[record.path] = ordered[0].name

    updated: list[BundleRecord] = []
    for record in records:
        if record.path in superseded:
            updated.append(_replace_record(record, recommendation="archive", reason=f"superseded by `{superseded[record.path]}`", superseded_by=superseded[record.path]))
        elif record.bundle_role in {"probe", "debug"}:
            updated.append(_replace_record(record, recommendation="archive", reason=f"{record.bundle_role} output is not a final ready bundle"))
        elif record.bundle_role == "ready" and record.path in keep_paths:
            updated.append(_replace_record(record, recommendation="keep", reason="latest ready bundle for this script/context"))
        elif record.bundle_role == "archive":
            updated.append(_replace_record(record, recommendation="keep", reason="already archived"))
        else:
            updated.append(_replace_record(record, recommendation="review", reason="unrecognized lifecycle state; review before moving"))
    return updated


def _replace_record(
    record: BundleRecord,
    *,
    recommendation: str,
    reason: str,
    superseded_by: str | None = None,
) -> BundleRecord:
    return BundleRecord(
        path=record.path,
        name=record.name,
        bundle_role=record.bundle_role,
        source_export_kind=record.source_export_kind,
        verification_state=record.verification_state,
        created_at=record.created_at,
        context_name=record.context_name,
        script_name=record.script_name,
        supersedes=record.supersedes,
        superseded_by=superseded_by or record.superseded_by,
        metadata_path=record.metadata_path,
        generation_manifest_path=record.generation_manifest_path,
        recommendation=recommendation,
        reason=reason,
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _created_sort_key(record: BundleRecord) -> str:
    return record.created_at or ""


def _unique_archive_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    for index in range(2, 1000):
        candidate = destination.with_name(f"{destination.name}_{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find unique archive destination for {destination}")


def _mark_archived(path: Path) -> None:
    metadata_path = path / "source" / "metadata.json"
    if not metadata_path.exists():
        return
    metadata = _read_json(metadata_path)
    lifecycle = metadata.setdefault("lifecycle", {})
    if isinstance(lifecycle, dict):
        lifecycle["bundle_role"] = "archive"
        lifecycle["archived_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    metadata["bundle_role"] = "archive"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
