"""Project-scoped `.zeia` import and context helpers."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from . import xml_compat as ET

from .archive_cache import archive_reference_fingerprint
from .config import (
    ACTIVE_CONTEXT_FILE,
    COLLECTIONS_DIR,
    PROJECTS_DIR,
    TEMP_FILES_DIRNAME,
)
from .import_identity import build_source_import_identity
from .progress import ProgressCallback, ProgressEmitter, ProgressStage
from .project_store import ProjectStore
from .runner import PipelineError
from .worktable_geometry import build_worktable_geometry
from tecan_common.zeia_limits import validate_zeia_archive_limits
from tecan_reader.project_model import CanonicalProjectModel, build_completeness_metadata
from tecan_reader.zeia_adapters import ZeiaFormatError, ingest_zeia, probe_zeia


XML_OBJECT_EXTS = {".xcmp", ".xwsp", ".xlqc", ".xlcp", ".xsit", ".xcon", ".xml"}
ASSET_EXTS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}
SNAPSHOT_TEXT_EXTS = {
    ".cfg",
    ".config",
    ".csv",
    ".html",
    ".htm",
    ".ini",
    ".json",
    ".log",
    ".md",
    ".reg",
    ".txt",
    ".xml",
}
SNAPSHOT_BINARY_EXTS = {".cab", ".dmp", ".dump", ".evtx"}
SNAPSHOT_TEXT_MAX_BYTES = 1024 * 1024
PROJECT_CONTEXT_XML_MAX_BYTES = 4 * 1024 * 1024
# Geometry parsing is an optional derived artifact; it never defines canonical
# project completeness or full-export readiness.
WORKTABLE_GEOMETRY_ENTRY_LIMIT = 2500
PROJECT_MANIFEST_SCHEMA_VERSION = 3
SUPPORTED_PROJECT_MANIFEST_SCHEMA_VERSIONS = frozenset({1, 2, 3})
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
FULL_ZEIA_ASK = (
    "Ask the user for a full FluentControl ZEIA export that includes the source "
    "scripts and their referenced worktables, liquid classes, labware/system "
    "objects, and other dependencies. Wait for that export, or get explicit "
    "permission before continuing with the current partial/non-full export."
)
FULL_ZEIA_WARNING_ONLY_FINDING_IDS = {
    "missing_liquid_class_objects",
    "missing_referenced_worktables",
    "unresolved_script_references",
}
FULL_ZEIA_WARNING_SUMMARY_SUFFIX = (
    "This appears to be stale dependency metadata from unrelated scripts in an "
    "otherwise dependency-rich full-system export."
)


@dataclass(frozen=True)
class ProjectContext:
    name: str
    root: Path
    manifest: dict[str, Any]

    @property
    def extracted_dir(self) -> Path:
        return self.root / "extracted"

    @property
    def artifacts_root(self) -> Path:
        """Return the complete workspace for this imported source context."""
        return self.root.resolve()

    @property
    def drafts_dir(self) -> Path:
        return self.artifacts_root / "drafts"

    @property
    def build_dir(self) -> Path:
        return self.artifacts_root / "build"

    @property
    def reports_dir(self) -> Path:
        return self.artifacts_root / "reports"

    @property
    def roundtrips_dir(self) -> Path:
        return self.artifacts_root / "roundtrips"


@dataclass(frozen=True)
class ProjectCollection:
    name: str
    root: Path
    manifest: dict[str, Any]

    @property
    def extracted_dir(self) -> Path:
        return self.root / "extracted"

    @property
    def artifacts_root(self) -> Path:
        """Return the complete workspace for this source collection."""
        return self.root.resolve()

    @property
    def drafts_dir(self) -> Path:
        return self.artifacts_root / "drafts"

    @property
    def build_dir(self) -> Path:
        return self.artifacts_root / "build"

    @property
    def reports_dir(self) -> Path:
        return self.artifacts_root / "reports"

    @property
    def roundtrips_dir(self) -> Path:
        return self.artifacts_root / "roundtrips"


ProjectLike = ProjectContext | ProjectCollection


def sanitize_project_name(raw: str | None, fallback: str) -> str:
    value = (raw or fallback).strip()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^A-Za-z0-9_.-]", "-", value)
    value = value.strip(".-_")
    if not value:
        raise PipelineError("project name is empty after sanitizing")
    if not PROJECT_NAME_RE.match(value):
        raise PipelineError(
            "project names must start with a letter or number and contain only "
            "letters, numbers, dots, underscores, or hyphens"
        )
    return value


def project_dir(name: str) -> Path:
    safe = sanitize_project_name(name, name)
    return (PROJECTS_DIR / safe / TEMP_FILES_DIRNAME).resolve()


def collection_dir(name: str) -> Path:
    safe = sanitize_project_name(name, name)
    return (COLLECTIONS_DIR / f"collection-{safe}" / TEMP_FILES_DIRNAME).resolve()


def manifest_path(name: str) -> Path:
    return project_dir(name) / "manifest.json"


def collection_manifest_path(name: str) -> Path:
    return collection_dir(name) / "manifest.json"


def _load_existing_project_manifest(root: Path) -> dict[str, Any] | None:
    path = root / "manifest.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def import_project(
    archive: Path,
    *,
    name: str | None = None,
    force: bool = False,
    snapshot_archives: list[Path] | None = None,
) -> ProjectContext:
    archive = archive.resolve()
    if not archive.exists():
        raise PipelineError(f"project archive not found: {archive}")
    if not zipfile.is_zipfile(archive):
        raise PipelineError(f"not a readable .zeia/zip archive: {archive}")
    snapshots = [
        Path(path).expanduser().resolve() for path in (snapshot_archives or [])
    ]
    for snapshot in snapshots:
        if not snapshot.exists():
            raise PipelineError(f"snapshot archive not found: {snapshot}")
        if not zipfile.is_zipfile(snapshot):
            raise PipelineError(
                f"not a readable FluentControl Snapshot zip archive: {snapshot}"
            )
    source_import_identity = build_source_import_identity(
        archive,
        snapshots,
        manifest_schema_version=PROJECT_MANIFEST_SCHEMA_VERSION,
    )

    try:
        canonical_model = _ingest_context_model(archive)
    except ZeiaFormatError as exc:
        raise PipelineError(str(exc)) from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise PipelineError(f"ZEIA canonical ingestion failed: {exc}") from exc

    project_name = sanitize_project_name(name, archive.stem)
    root = project_dir(project_name)
    if root.exists():
        existing = _load_existing_project_manifest(root)
        existing_identity = (existing or {}).get("source_import_identity")
        if existing and existing_identity == source_import_identity:
            return ProjectContext(project_name, root, existing)
        if not force:
            raise PipelineError(
                f"project context already exists: {project_name}. "
                "Use --force to replace it."
            )
        _remove_project_dir(root)

    source_dir = root / "source"
    extracted_dir = root / "extracted"
    source_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, source_dir / archive.name)

    try:
        with zipfile.ZipFile(archive) as zf:
            infos = validate_zeia_archive_limits(zf)
            names = [info.filename for info in infos]
            _safe_extract(zf, extracted_dir)
    except zipfile.BadZipFile as exc:
        raise PipelineError(
            f"not a readable .zeia/zip archive: {archive} ({exc})"
        ) from exc

    entries = list(names)
    snapshot_sources = _import_snapshot_archives(
        snapshots,
        source_dir=source_dir,
        extracted_dir=extracted_dir,
        entries=entries,
    )

    manifest = build_manifest(
        project_name=project_name,
        archive=archive,
        copied_archive=source_dir / archive.name,
        root=root,
        extracted_dir=extracted_dir,
        entries=entries,
        snapshot_archives=snapshot_sources,
        source_import_identity=source_import_identity,
        canonical_model=canonical_model,
    )
    _write_manifest(root, manifest)
    _project_store().write_text(
        root / "project_report.md", render_project_report(manifest)
    )

    return ProjectContext(project_name, root, manifest)


def normalize_project_manifest(
    manifest: Mapping[str, Any] | dict[str, Any],
    *,
    persist_path: Path | None = None,
) -> dict[str, Any]:
    """Migrate a supported project manifest or reject an unknown schema."""
    payload = dict(manifest)
    raw = payload.get("schema_version", 1)
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            "unsupported project manifest schema_version "
            f"{raw!r}; re-import the ZEIA with current protocol-builder "
            f"(supported versions: {sorted(SUPPORTED_PROJECT_MANIFEST_SCHEMA_VERSIONS)})"
        ) from exc
    if version not in SUPPORTED_PROJECT_MANIFEST_SCHEMA_VERSIONS:
        raise PipelineError(
            "unsupported project manifest schema_version "
            f"{version}; re-import the ZEIA with current protocol-builder "
            f"(supported versions: {sorted(SUPPORTED_PROJECT_MANIFEST_SCHEMA_VERSIONS)}, "
            f"current {PROJECT_MANIFEST_SCHEMA_VERSION})"
        )
    if version == PROJECT_MANIFEST_SCHEMA_VERSION:
        return payload
    payload["schema_version"] = PROJECT_MANIFEST_SCHEMA_VERSION
    if persist_path is not None:
        persist_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return payload


def load_project(name: str | None = None) -> ProjectContext:
    resolved_name = name or active_project_name()
    if not resolved_name:
        raise PipelineError(
            "no project context selected; pass --context or run use-project"
        )
    path = manifest_path(resolved_name)
    if not path.exists():
        raise PipelineError(f"project context not found: {resolved_name}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest = normalize_project_manifest(manifest, persist_path=path)
    return ProjectContext(resolved_name, project_dir(resolved_name), manifest)


def create_project_collection(
    name: str,
    project_names: list[str],
    *,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> ProjectCollection:
    """Create a persistent generation collection from imported project contexts."""
    collection_name = sanitize_project_name(name, name)
    if not project_names:
        raise PipelineError(
            "project collection must include at least one project context"
        )
    stages = tuple(
        ProgressStage(
            f"load_context_{index}", f"Loading source context: {project_name}"
        )
        for index, project_name in enumerate(project_names, start=1)
    ) + (
        ProgressStage("resolve_source_identities", "Resolving source identities"),
        ProgressStage("merge_scripts", "Merging scripts"),
        ProgressStage("merge_objects", "Merging objects"),
        ProgressStage("validate_collection", "Validating collection"),
        ProgressStage("write_manifest", "Writing manifest"),
    )
    progress = ProgressEmitter(
        stages,
        progress_callback,
        operation_id="create_collection",
    )
    root = collection_dir(collection_name)
    if root.exists():
        if not force:
            raise PipelineError(
                f"project collection already exists: {collection_name}. "
                "Use --force to replace it."
            )
        _remove_collection_dir(root)

    contexts: list[ProjectContext] = []
    for index, project_name in enumerate(project_names, start=1):
        stage_id = f"load_context_{index}"
        progress.started(stage_id)
        try:
            context = load_project(project_name)
        except Exception as exc:
            progress.failed(stage_id, str(exc))
            raise
        contexts.append(context)
        script_count = len(context.manifest.get("scripts") or [])
        object_count = len(context.manifest.get("objects") or [])
        progress.completed(
            stage_id,
            f"Loaded {script_count:,} scripts and {object_count:,} objects.",
        )

    try:
        root.mkdir(parents=True, exist_ok=True)
        manifest = build_collection_manifest(
            collection_name=collection_name,
            contexts=contexts,
            root=root,
            progress=progress,
        )
        progress.started("write_manifest")
        with progress.heartbeat(
            "write_manifest",
            message="Serializing and publishing collection artifacts.",
        ):
            _write_manifest(root, manifest)
            _project_store().write_text(
                root / "project_report.md",
                render_project_collection_report(manifest),
            )
        progress.completed(
            "write_manifest", "Manifest and collection report published."
        )
    except Exception as exc:
        progress.failed_current(str(exc))
        raise
    return ProjectCollection(collection_name, root, manifest)


def load_project_collection(name: str) -> ProjectCollection:
    collection_name = sanitize_project_name(name, name)
    path = collection_manifest_path(collection_name)
    if not path.exists():
        raise PipelineError(f"project collection not found: {collection_name}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return ProjectCollection(collection_name, collection_dir(collection_name), manifest)


def list_project_collections() -> list[dict[str, Any]]:
    if not COLLECTIONS_DIR.exists():
        return []
    out = []
    for project_dir_path in sorted(COLLECTIONS_DIR.iterdir()):
        if not project_dir_path.is_dir() or not project_dir_path.name.startswith(
            "collection-"
        ):
            continue
        path = project_dir_path / TEMP_FILES_DIRNAME
        manifest_file = path / "manifest.json"
        if not manifest_file.exists():
            continue
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        source_projects = manifest.get("source_projects") or []
        out.append(
            {
                "name": manifest.get("name") or path.name,
                "created_at": manifest.get("created_at"),
                "project_count": len(source_projects),
                "projects": [
                    item.get("name") for item in source_projects if item.get("name")
                ],
                "script_count": len(manifest.get("scripts") or []),
                "workspace_count": len(manifest.get("workspaces") or []),
                "object_count": len(manifest.get("objects") or []),
                "snapshot_evidence_count": len(manifest.get("snapshot_evidence") or []),
                "root": str(path.resolve()),
            }
        )
    return out


def list_projects() -> list[dict[str, Any]]:
    if not PROJECTS_DIR.exists():
        return []
    out = []
    for project_dir_path in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir_path.is_dir() or project_dir_path.name.startswith(
            (".", "_", "collection-")
        ):
            continue
        path = project_dir_path / TEMP_FILES_DIRNAME
        manifest_file = path / "manifest.json"
        if not manifest_file.exists():
            continue
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append(
            {
                "name": manifest.get("name") or path.name,
                "imported_at": manifest.get("imported_at"),
                "source_archive": manifest.get("source_archive"),
                "script_count": len(manifest.get("scripts") or []),
                "workspace_count": len(manifest.get("workspaces") or []),
                "object_count": len(manifest.get("objects") or []),
                "snapshot_evidence_count": len(manifest.get("snapshot_evidence") or []),
                "root": str(path.resolve()),
            }
        )
    return out


def set_active_project(name: str) -> ProjectContext:
    ctx = load_project(name)
    _project_store().set_active_context(ctx.name)
    return ctx


def clear_active_project() -> None:
    _project_store().clear_active_context()


def active_project_name() -> str | None:
    return _project_store().active_context_name()


def resolve_context_path(ctx: ProjectLike | None, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    if ctx is not None:
        artifact_root_attrs = {
            "drafts": "drafts_dir",
            "build": "build_dir",
            "reports": "reports_dir",
            "roundtrips": "roundtrips_dir",
            "extracted": "extracted_dir",
        }
        root_attr = artifact_root_attrs.get(path.parts[0]) if path.parts else None
        artifact_root = getattr(ctx, root_attr, None) if root_attr else None
        if artifact_root is not None:
            return (Path(artifact_root).joinpath(*path.parts[1:])).resolve()
        candidate = (ctx.root / path).resolve()
        if candidate.exists():
            return candidate
    return (Path.cwd() / path).resolve()


def resolve_context_script(ctx: ProjectLike | None, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    if ctx is None:
        return (Path.cwd() / path).resolve()

    project_relative = (ctx.root / path).resolve()
    if project_relative.exists():
        return project_relative

    cwd_relative = (Path.cwd() / path).resolve()
    if cwd_relative.exists():
        return cwd_relative

    text = str(value)
    matches = []
    for script in ctx.manifest.get("scripts", []):
        if text in _script_resolution_candidates(script):
            matches.append(script)
    if len(matches) == 1:
        return _script_resolved_path(ctx, matches[0])
    if len(matches) > 1:
        names = ", ".join(_qualified_script_label(match) for match in matches[:5])
        raise PipelineError(f"ambiguous project script {value!r}; matches: {names}")

    return project_relative


PROJECT_QUERY_DEFAULT_LIMIT = 20
PROJECT_QUERY_MAX_LIMIT = 50

_COMPACT_MATCH_KEYS: tuple[str, ...] = (
    "kind",
    "object_name",
    "entry",
    "qualified_name",
    "qualified_entry",
    "type_id",
    "functional_group",
    "folder",
    "source",
    "source_context",
    "extracted_path",
    "guid",
    "script_guid",
    "category",
    "archive_kind",
    "summary",
)


def clamp_project_query_limit(limit: int | None) -> int:
    """Clamp query limit to the MCP/CLI safe range."""
    if limit is None:
        return PROJECT_QUERY_DEFAULT_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise PipelineError(f"invalid project query limit: {limit!r}") from exc
    if value < 1:
        raise PipelineError("project query limit must be >= 1")
    return min(value, PROJECT_QUERY_MAX_LIMIT)


def compact_full_zeia_export(payload: Any) -> dict[str, Any] | None:
    """Keep export-check status fields; drop large warning lists."""
    if not isinstance(payload, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("required", "status", "accepted", "summary"):
        if key in payload:
            out[key] = payload.get(key)
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        out["warning_count"] = len(warnings)
    blocking = payload.get("blocking_findings")
    if isinstance(blocking, list):
        out["blocking_finding_count"] = len(blocking)
    return out


def compact_project_summary(ctx: ProjectLike) -> dict[str, Any]:
    """Token-cheap project summary for MCP/CLI JSON (no script/object arrays)."""
    manifest = ctx.manifest if isinstance(getattr(ctx, "manifest", None), dict) else {}
    completeness = manifest.get("inspection_completeness") or {}
    summary: dict[str, Any] = {
        "script_count": len(manifest.get("scripts") or []),
        "workspace_count": len(manifest.get("workspaces") or []),
        "object_count": len(manifest.get("objects") or []),
        "worklist_count": len(manifest.get("worklists") or []),
        "snapshot_evidence_count": len(manifest.get("snapshot_evidence") or []),
        "entry_count": manifest.get("entry_count"),
        "inspection_complete": completeness.get("complete"),
        "inspection_mode": completeness.get("mode"),
        "inspection_limits": completeness.get("configured_limits") or {},
        "truncated_scripts": completeness.get("truncated_scripts", 0),
        "truncated_objects": completeness.get("truncated_objects", 0),
        "oversized_member_count": completeness.get("oversized_member_count", 0),
        "source_archive": manifest.get("source_archive"),
        "imported_at": manifest.get("imported_at"),
    }
    export_check = compact_full_zeia_export(manifest.get("full_zeia_export"))
    if export_check is not None:
        summary["full_zeia_export"] = export_check
    return summary


def compact_project_match(item: Mapping[str, Any]) -> dict[str, Any]:
    """Project a find hit to small fields only (no dependency trees)."""
    out: dict[str, Any] = {}
    for key in _COMPACT_MATCH_KEYS:
        value = item.get(key)
        if value is None or value == "" or value == []:
            continue
        if key == "summary" and isinstance(value, str) and len(value) > 240:
            out[key] = value[:237] + "..."
        else:
            out[key] = value
    return out


def find_in_project(
    ctx: ProjectLike,
    pattern: str,
    *,
    kind: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    needle = pattern.casefold()
    matches: list[dict[str, Any]] = []

    def add(item: dict[str, Any], item_kind: str) -> None:
        if kind and item_kind != kind:
            return
        haystacks = [
            item_kind,
            item.get("entry", ""),
            item.get("source", ""),
            item.get("source_context", ""),
            item.get("qualified_name", ""),
            item.get("qualified_entry", ""),
            item.get("object_name", ""),
            item.get("type_id", ""),
            item.get("functional_group", ""),
            item.get("footprint", ""),
            item.get("renderer", ""),
            item.get("category", ""),
            item.get("archive_kind", ""),
            item.get("summary", ""),
        ]
        haystacks.extend(str(value) for value in item.get("names", []) or [])
        haystacks.extend(str(value) for value in item.get("guids", []) or [])
        haystacks.extend(str(value) for value in item.get("roles", []) or [])
        haystacks.extend(str(value) for value in item.get("signals", []) or [])
        extracted_fields = item.get("extracted_fields") or {}
        if isinstance(extracted_fields, dict):
            for key, values in extracted_fields.items():
                haystacks.append(str(key))
                if isinstance(values, list):
                    haystacks.extend(str(value) for value in values)
                elif values:
                    haystacks.append(str(values))
        dependencies = item.get("dependencies") or {}
        if isinstance(dependencies, dict):
            for values in dependencies.values():
                if isinstance(values, list):
                    haystacks.extend(str(value) for value in values)
                elif values:
                    haystacks.append(str(values))
        references = item.get("references") or []
        if isinstance(references, list):
            for ref in references:
                if isinstance(ref, dict):
                    haystacks.extend(str(value) for value in ref.values() if value)
        if any(needle in str(value).casefold() for value in haystacks if value):
            matches.append({"kind": item_kind, **item})

    for script in ctx.manifest.get("scripts", []):
        add(script, "script")
    for obj in ctx.manifest.get("objects", []):
        add(obj, obj.get("kind") or "object")
    for worklist in ctx.manifest.get("worklists", []):
        add(worklist, "worklist")
    for snapshot in ctx.manifest.get("snapshot_evidence", []):
        add(snapshot, "snapshot")
    for alias in ctx.manifest.get("catalog_alias_candidates", []):
        add(alias, "catalog_alias")
    return matches[:limit]


def query_project(
    ctx: ProjectLike,
    pattern: str,
    *,
    kind: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Search a project context and return compact, capped matches for agents."""
    capped = clamp_project_query_limit(limit)
    raw = find_in_project(ctx, pattern, kind=kind, limit=capped + 1)
    truncated = len(raw) > capped
    matches = [compact_project_match(item) for item in raw[:capped]]
    return {
        "ok": True,
        "context": getattr(ctx, "name", None),
        "pattern": pattern,
        "kind": kind,
        "limit": capped,
        "match_count": len(matches),
        "truncated": truncated,
        "matches": matches,
        "hint": "Refine pattern/kind or raise limit (max 50); do not read manifest.json into chat",
    }


def inspection_payload(
    ctx: ProjectLike, *, report_path: Path | None = None
) -> dict[str, Any]:
    """Compact inspect payload: summary + path pointers, never full manifest."""
    root = Path(getattr(ctx, "root"))
    resolved_report = report_path
    if resolved_report is None:
        candidate = root / "project_report.md"
        resolved_report = candidate if candidate.exists() else None
    return {
        "ok": True,
        "name": getattr(ctx, "name", None),
        "root": str(root),
        "report_path": str(resolved_report) if resolved_report else None,
        "manifest_path": str(root / "manifest.json"),
        "summary": compact_project_summary(ctx),
        "query_hint": "Use fluent_project_query / project-find; do not read manifest.json into chat",
    }


def _ingest_context_model(archive: Path) -> CanonicalProjectModel:
    """Ingest ZEIA through the reader, preserving snapshot-only compatibility."""
    try:
        return ingest_zeia(archive)
    except ZeiaFormatError as exc:
        if exc.result.status != "unsupported" or not _is_support_context_archive(
            exc.result.entries
        ):
            raise
        entries = list(exc.result.entries)
        return CanonicalProjectModel(
            source_archive=str(archive.resolve()),
            adapter_id="support-context",
            detection={**exc.result.to_dict(), "context_kind": "support"},
            scripts=[],
            objects=[],
            worklists=[],
            errors=[],
            source_metadata={
                "entry_count": len(entries),
                "extension_counts": dict(
                    sorted(
                        Counter(
                            Path(entry).suffix.lower() or "<none>"
                            for entry in entries
                        ).items()
                    )
                ),
                "script_count_total": 0,
                "context_kind": "support",
                "completeness": build_completeness_metadata(
                    script_limit=None,
                    object_limit=None,
                    eligible_member_counts={"scripts": 0, "objects": 0, "worklists": 0},
                    summarized_counts={"scripts": 0, "objects": 0, "worklists": 0},
                ),
            },
            completeness=build_completeness_metadata(
                script_limit=None,
                object_limit=None,
                eligible_member_counts={"scripts": 0, "objects": 0, "worklists": 0},
                summarized_counts={"scripts": 0, "objects": 0, "worklists": 0},
            ),
        )


def _is_support_context_archive(entries: tuple[str, ...] | list[str]) -> bool:
    return any(
        _snapshot_roles_for_entry(entry, Path(entry).suffix.lower(), "")
        for entry in entries
    )


def build_manifest(
    *,
    project_name: str,
    archive: Path,
    copied_archive: Path,
    root: Path,
    extracted_dir: Path,
    entries: list[str],
    snapshot_archives: list[dict[str, Any]] | None = None,
    source_import_identity: dict[str, Any],
    canonical_model: CanonicalProjectModel | None = None,
) -> dict[str, Any]:
    if canonical_model is None:
        try:
            canonical_model = _ingest_context_model(archive)
        except ZeiaFormatError as exc:
            raise PipelineError(str(exc)) from exc
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise PipelineError(f"ZEIA canonical ingestion failed: {exc}") from exc
    else:
        if Path(canonical_model.source_archive).resolve() != archive.resolve():
            raise PipelineError(
                "canonical model source archive does not match the project archive"
            )

    scripts, objects, worklists = canonical_model.context_records(root=root)
    snapshot_evidence = []
    errors = list(canonical_model.errors)
    completeness = dict(
        canonical_model.completeness
        or canonical_model.source_metadata.get("completeness")
        or {}
    )
    if not completeness:
        completeness = build_completeness_metadata(
            script_limit=None,
            object_limit=None,
            eligible_member_counts={
                "scripts": len(scripts),
                "objects": len(objects),
                "worklists": len(worklists),
            },
            summarized_counts={
                "scripts": len(scripts),
                "objects": len(objects),
                "worklists": len(worklists),
            },
            errors=errors,
        )
    detailed_geometry = len(entries) <= WORKTABLE_GEOMETRY_ENTRY_LIMIT

    # Snapshot/support evidence is deliberately separate from ZEIA semantics;
    # canonical project-reader records own every script/object/worklist above.
    for entry in entries:
        suffix = Path(entry).suffix.lower()
        extracted_path = extracted_dir / _zip_entry_to_path(entry)
        relative = extracted_path.relative_to(root).as_posix()
        if not _should_inspect_snapshot_evidence(entry, suffix):
            continue
        try:
            snapshot = _inspect_snapshot_evidence(
                extracted_path, entry, relative, suffix
            )
            if snapshot is not None:
                snapshot_evidence.append(snapshot)
        except Exception as exc:
            errors.append({"entry": entry, "error": f"snapshot evidence: {exc}"})

    extension_counts = dict(
        sorted(
            Counter(Path(entry).suffix.lower() or "<none>" for entry in entries).items()
        )
    )
    all_object_names = sorted(
        {
            item.get("object_name", "")
            for item in [*scripts, *objects]
            if item.get("object_name")
        }
    )
    project_names = sorted(
        set(all_object_names)
        | {name for obj in objects for name in obj.get("names", []) if name}
    )
    liquid_classes = sorted(
        {
            name
            for script in scripts
            for name in script.get("dependencies", {}).get("liquid_classes", [])
        }
        | {
            obj.get("object_name", "")
            for obj in objects
            if obj.get("kind") == "liquid_class" and obj.get("object_name")
        }
    )
    labware_names = sorted(
        {
            name
            for script in scripts
            for name in script.get("dependencies", {}).get("labware_names", [])
        }
    )
    rack_types = sorted(
        {
            name
            for script in scripts
            for name in script.get("dependencies", {}).get("rack_types", [])
        }
    )
    worklist_paths = sorted(
        {
            str(worklist.get("source") or worklist.get("entry") or "")
            for worklist in worklists
            if worklist.get("source") or worklist.get("entry")
        }
        | {
            name
            for script in scripts
            for name in script.get("dependencies", {}).get(
                "external_or_worklist_refs", []
            )
            if str(name).lower().endswith(".gwl")
        }
    )
    workspaces = [obj for obj in objects if obj.get("kind") == "workspace"]
    custom_part_summary = _custom_part_summary(objects, scripts)
    snapshot_summary = _snapshot_summary(snapshot_evidence)
    archive_kind = _archive_kind(scripts, objects, worklists, snapshot_evidence)

    manifest = {
        "schema_version": PROJECT_MANIFEST_SCHEMA_VERSION,
        "kind": archive_kind,
        "name": project_name,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_archive": str(archive),
        "source_archive_fingerprint": archive_reference_fingerprint(archive),
        "source_import_identity": source_import_identity,
        "copied_archive": str(copied_archive),
        "snapshot_archives": snapshot_archives or [],
        "root": str(root),
        "extracted_dir": str(extracted_dir),
        "entry_count": len(entries),
        "extension_counts": extension_counts,
        "scripts": scripts,
        "objects": objects,
        "worklists": worklists,
        "workspaces": workspaces,
        "canonical_model": {
            "schema_version": canonical_model.schema_version,
            "adapter_id": canonical_model.adapter_id,
            "detection": canonical_model.detection,
            "source_metadata": canonical_model.source_metadata,
            "completeness": completeness,
        },
        "inspection_completeness": completeness,
        "snapshot_evidence": snapshot_evidence,
        "snapshot_summary": snapshot_summary,
        "custom_part_summary": custom_part_summary,
        "object_names": all_object_names,
        "project_names": project_names,
        "liquid_classes": liquid_classes,
        "labware_names": labware_names,
        "rack_types": rack_types,
        "worklist_paths": worklist_paths,
        # Worktable labware/rack labels like FilterDWP[001] are instance IDs, not
        # catalog aliases. Only mine project/object names for catalog alias hints.
        "catalog_alias_candidates": _alias_candidates(project_names),
        "xml_inspection_mode": "complete" if completeness.get("complete") else "preview",
        "errors": errors,
    }
    manifest["full_zeia_export"] = assess_full_zeia_export(manifest)
    if detailed_geometry:
        manifest["worktable_geometry"] = build_worktable_geometry(
            manifest,
            max_xml_bytes=PROJECT_CONTEXT_XML_MAX_BYTES,
        )
    else:
        manifest["worktable_geometry"] = _skipped_large_export_geometry(workspaces)
    return manifest


def build_collection_manifest(
    *,
    collection_name: str,
    contexts: list[ProjectContext],
    root: Path,
    progress: ProgressEmitter | None = None,
) -> dict[str, Any]:
    scripts: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    worklists: list[dict[str, Any]] = []
    workspaces: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    extension_counts: Counter[str] = Counter()
    entry_count = 0
    source_projects: list[dict[str, Any]] = []
    source_archives: list[str] = []
    copied_archives: list[str] = []
    snapshot_archives: list[dict[str, Any]] = []
    snapshot_evidence: list[dict[str, Any]] = []
    canonical_models: list[dict[str, Any]] = []

    object_names: set[str] = set()
    project_names: set[str] = set()
    liquid_classes: set[str] = set()
    labware_names: set[str] = set()
    rack_types: set[str] = set()
    worklist_paths: set[str] = set()
    source_worklist_paths: list[dict[str, str]] = []
    alias_candidates: dict[tuple[str, str], dict[str, str]] = {}
    custom_part_counters: Counter[str] = Counter()
    custom_pin_refs: set[str] = set()
    custom_asset_refs: set[str] = set()

    if progress is not None:
        progress.started("resolve_source_identities")
    for ctx in contexts:
        manifest = ctx.manifest
        entry_count += int(manifest.get("entry_count") or 0)
        extension_counts.update(manifest.get("extension_counts") or {})

        if manifest.get("source_archive"):
            source_archives.append(str(manifest["source_archive"]))
        if manifest.get("copied_archive"):
            copied_archives.append(str(manifest["copied_archive"]))

        source_projects.append(
            {
                "name": ctx.name,
                "root": str(ctx.root),
                "manifest": str(ctx.root / "manifest.json"),
                "project_report": str(ctx.root / "project_report.md"),
                "source_archive": manifest.get("source_archive"),
                "copied_archive": manifest.get("copied_archive"),
                "script_count": len(manifest.get("scripts") or []),
                "workspace_count": len(manifest.get("workspaces") or []),
                "object_count": len(manifest.get("objects") or []),
                "worklist_count": len(manifest.get("worklists") or []),
                "snapshot_evidence_count": len(manifest.get("snapshot_evidence") or []),
            }
        )

        canonical = manifest.get("canonical_model")
        if isinstance(canonical, dict):
            canonical_models.append({"source_context": ctx.name, **canonical})
        for worklist in manifest.get("worklists") or []:
            worklists.append(_collection_item(ctx, worklist))
        for snapshot in manifest.get("snapshot_evidence") or []:
            snapshot_evidence.append(_collection_item(ctx, snapshot))
        for snapshot_archive in manifest.get("snapshot_archives") or []:
            if isinstance(snapshot_archive, dict):
                snapshot_archives.append(
                    {"source_context": ctx.name, **snapshot_archive}
                )

        object_names.update(
            str(name) for name in manifest.get("object_names") or [] if name
        )
        project_names.update(
            str(name) for name in manifest.get("project_names") or [] if name
        )
        liquid_classes.update(
            str(name) for name in manifest.get("liquid_classes") or [] if name
        )
        labware_names.update(
            str(name) for name in manifest.get("labware_names") or [] if name
        )
        rack_types.update(
            str(name) for name in manifest.get("rack_types") or [] if name
        )

        for path in manifest.get("worklist_paths") or []:
            worklist_paths.add(str(path))
            source_worklist_paths.append(
                {"source_context": ctx.name, "path": str(path)}
            )

        for alias in manifest.get("catalog_alias_candidates") or []:
            base_name = str(alias.get("base_name") or "")
            project_name = str(alias.get("project_name") or "")
            key = (base_name, project_name)
            if base_name and project_name and key not in alias_candidates:
                alias_candidates[key] = {
                    "base_name": base_name,
                    "project_name": project_name,
                }

        for error in manifest.get("errors") or []:
            errors.append({"source_context": ctx.name, **error})
    if progress is not None:
        progress.completed(
            "resolve_source_identities",
            f"Resolved {len(contexts):,} source contexts.",
            completed_units=len(contexts),
            total_units=len(contexts),
            unit_name="contexts",
        )

    total_scripts = sum(len(ctx.manifest.get("scripts") or []) for ctx in contexts)
    if progress is not None:
        progress.started(
            "merge_scripts", total_units=total_scripts, unit_name="scripts"
        )
    merged_script_count = 0
    for ctx in contexts:
        for script in ctx.manifest.get("scripts") or []:
            item = _collection_item(ctx, script)
            scripts.append(item)
            deps = item.get("dependencies") or {}
            if deps.get("pin_refs") or deps.get("worktable_pin_locations"):
                custom_part_counters["script_pin_refs"] += 1
            if deps.get("custom_asset_refs"):
                custom_part_counters["script_asset_refs"] += 1
            custom_pin_refs.update(
                str(value) for value in deps.get("pin_refs") or [] if value
            )
            custom_pin_refs.update(
                str(value)
                for value in deps.get("worktable_pin_locations") or []
                if value
            )
            custom_asset_refs.update(
                str(value) for value in deps.get("custom_asset_refs") or [] if value
            )
            merged_script_count += 1
            if progress is not None and _should_report_collection_progress(
                merged_script_count,
                total_scripts,
                interval=10,
            ):
                progress.running(
                    "merge_scripts",
                    completed_units=merged_script_count,
                    total_units=total_scripts,
                    unit_name="scripts",
                )
    if progress is not None:
        progress.completed(
            "merge_scripts",
            completed_units=merged_script_count,
            total_units=total_scripts,
            unit_name="scripts",
        )

    total_objects = sum(len(ctx.manifest.get("objects") or []) for ctx in contexts)
    if progress is not None:
        progress.started(
            "merge_objects", total_units=total_objects, unit_name="objects"
        )
    merged_object_count = 0
    for ctx in contexts:
        for obj in ctx.manifest.get("objects") or []:
            item = _collection_item(ctx, obj)
            objects.append(item)
            if item.get("kind") == "workspace":
                workspaces.append(item)
            _update_custom_part_rollup(
                custom_part_counters, custom_pin_refs, custom_asset_refs, item
            )
            merged_object_count += 1
            if progress is not None and _should_report_collection_progress(
                merged_object_count,
                total_objects,
                interval=1000,
            ):
                progress.running(
                    "merge_objects",
                    completed_units=merged_object_count,
                    total_units=total_objects,
                    unit_name="objects",
                )
    if progress is not None:
        progress.completed(
            "merge_objects",
            completed_units=merged_object_count,
            total_units=total_objects,
            unit_name="objects",
        )
        progress.started(
            "validate_collection", "Checking duplicate collection identities."
        )

    source_completeness = [
        ctx.manifest.get("inspection_completeness") or {}
        for ctx in contexts
    ]
    oversized_members = sorted(
        {
            str(entry)
            for item in source_completeness
            for entry in item.get("oversized_members") or []
            if entry
        }
    )
    collection_completeness = {
        "complete": all(item.get("complete", False) for item in source_completeness),
        "mode": "complete"
        if all(item.get("mode") == "complete" for item in source_completeness)
        else "preview",
        "configured_limits": {
            "scripts": None,
            "objects": None,
        },
        "eligible_member_counts": {
            key: sum(
                int((item.get("eligible_member_counts") or {}).get(key) or 0)
                for item in source_completeness
            )
            for key in ("scripts", "objects", "worklists")
        },
        "summarized_counts": {
            "scripts": len(scripts),
            "objects": len(objects),
            "worklists": len(worklists),
        },
        "truncated_scripts": sum(
            int(item.get("truncated_scripts") or 0) for item in source_completeness
        ),
        "truncated_objects": sum(
            int(item.get("truncated_objects") or 0) for item in source_completeness
        ),
        "error_count": len(errors),
        "blocking_error_count": sum(
            1
            for error in errors
            if str(error.get("severity") or "error") != "warning"
        ),
        "oversized_member_count": len(oversized_members),
        "oversized_members": oversized_members[:50],
    }
    manifest = {
        "schema_version": 1,
        "kind": "project_collection",
        "name": collection_name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "source_projects": source_projects,
        "source_archives": _dedupe_strings(source_archives),
        "copied_archives": _dedupe_strings(copied_archives),
        "entry_count": entry_count,
        "extension_counts": dict(sorted(extension_counts.items())),
        "scripts": scripts,
        "objects": objects,
        "worklists": worklists,
        "workspaces": workspaces,
        "canonical_models": canonical_models,
        "inspection_completeness": collection_completeness,
        "snapshot_archives": snapshot_archives,
        "snapshot_evidence": snapshot_evidence,
        "snapshot_summary": _snapshot_summary(snapshot_evidence),
        "custom_part_summary": _custom_part_summary_payload(
            custom_part_counters,
            custom_pin_refs,
            custom_asset_refs,
        ),
        "object_names": sorted(object_names),
        "project_names": sorted(project_names),
        "liquid_classes": sorted(liquid_classes),
        "labware_names": sorted(labware_names),
        "rack_types": sorted(rack_types),
        "worklist_paths": sorted(worklist_paths),
        "source_worklist_paths": source_worklist_paths,
        "catalog_alias_candidates": list(alias_candidates.values()),
        "errors": errors,
    }
    if progress is None:
        manifest["full_zeia_export"] = _collection_full_zeia_assessment(contexts)
        manifest["worktable_geometry"] = _merge_context_worktable_geometry(
            contexts
        ) or build_worktable_geometry(
            manifest, max_xml_bytes=PROJECT_CONTEXT_XML_MAX_BYTES
        )
    else:
        with progress.heartbeat(
            "validate_collection",
            message="Checking collection structure and worktable geometry.",
        ):
            manifest["full_zeia_export"] = _collection_full_zeia_assessment(contexts)
            manifest["worktable_geometry"] = _merge_context_worktable_geometry(
                contexts
            ) or build_worktable_geometry(
                manifest, max_xml_bytes=PROJECT_CONTEXT_XML_MAX_BYTES
            )
    _validate_collection_manifest(manifest, progress=progress)
    if progress is not None:
        progress.completed(
            "validate_collection", "Collection structure and identities are valid."
        )
    return manifest


def _merge_context_worktable_geometry(contexts: list[ProjectContext]) -> dict[str, Any]:
    """Reuse imported per-context geometry instead of reparsing every collection XML file."""
    components: dict[str, dict[str, Any]] = {}
    sites: dict[str, dict[str, Any]] = {}
    connectors: dict[str, dict[str, Any]] = {}
    pin_sites: dict[str, dict[str, Any]] = {}
    nest_cap_sites: dict[str, dict[str, Any]] = {}
    workspaces: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    saw_geometry = False

    for ctx in contexts:
        geometry = ctx.manifest.get("worktable_geometry") or {}
        if not isinstance(geometry, dict) or not geometry:
            continue
        saw_geometry = True
        _merge_geometry_items(components, ctx, geometry.get("components") or [])
        _merge_geometry_items(sites, ctx, geometry.get("sites") or [])
        _merge_geometry_items(connectors, ctx, geometry.get("connectors") or [])
        _merge_geometry_items(pin_sites, ctx, geometry.get("pin_sites") or [])
        nest_cap_source = geometry.get("nest_cap_sites") or [
            site
            for site in (geometry.get("pin_sites") or [])
            if isinstance(site, dict) and site.get("site_kind") == "cap_nest"
        ]
        _merge_geometry_items(nest_cap_sites, ctx, nest_cap_source)
        for item in geometry.get("workspaces") or []:
            if isinstance(item, dict):
                workspaces.append(_context_geometry_item(ctx, item))
        for item in geometry.get("errors") or []:
            if isinstance(item, dict):
                errors.append(_context_geometry_item(ctx, item))

    if not saw_geometry:
        return {}

    return {
        "schema_version": "tecan.worktable_geometry.v1",
        "component_count": len(components),
        "site_count": len(sites),
        "connector_count": len(connectors),
        "workspace_count": len(workspaces),
        "components": sorted(
            components.values(),
            key=lambda item: (item.get("name") or "", item.get("guid") or ""),
        ),
        "sites": sorted(
            sites.values(),
            key=lambda item: (
                item.get("pin_name") or item.get("location_group_name") or "",
                item.get("guid") or "",
            ),
        ),
        "connectors": sorted(
            connectors.values(),
            key=lambda item: (
                item.get("site_name") or "",
                item.get("component_name") or "",
                item.get("guid") or "",
            ),
        ),
        "workspaces": sorted(
            workspaces,
            key=lambda item: (item.get("name") or "", item.get("guid") or ""),
        ),
        "pin_sites": sorted(
            pin_sites.values(),
            key=lambda item: item.get("pin_name") or item.get("guid") or "",
        ),
        "nest_cap_sites": sorted(
            nest_cap_sites.values(),
            key=lambda item: item.get("pin_name") or item.get("guid") or "",
        ),
        "errors": errors,
    }


def _skipped_large_export_geometry(workspaces: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "tecan.worktable_geometry.v1",
        "component_count": 0,
        "site_count": 0,
        "connector_count": 0,
        "workspace_count": len(workspaces),
        "components": [],
        "sites": [],
        "connectors": [],
        "workspaces": [],
        "pin_sites": [],
        "nest_cap_sites": [],
        "errors": [
            {
                "reason": "large_export_geometry_skipped",
                "message": (
                    "Detailed worktable geometry was skipped for a large full-export "
                    "context; use the script-specific source context geometry for generation."
                ),
            }
        ],
    }


def _merge_geometry_items(
    target: dict[str, dict[str, Any]],
    ctx: ProjectContext,
    items: list[dict[str, Any]],
) -> None:
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        merged = _context_geometry_item(ctx, item)
        key = _geometry_item_key(merged, fallback=f"{ctx.name}:{len(target)}:{index}")
        target[key] = merged


def _context_geometry_item(ctx: ProjectContext, item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out.setdefault("source_context", ctx.name)
    out.setdefault("context_root", str(ctx.root))
    out.setdefault("context_manifest", str(ctx.root / "manifest.json"))
    return out


def _geometry_item_key(item: dict[str, Any], *, fallback: str) -> str:
    for field in ("guid", "path", "object_name", "name"):
        value = item.get(field)
        if value:
            return str(value)
    return fallback


def assess_full_zeia_export(manifest: dict[str, Any]) -> dict[str, Any]:
    """Conservatively assess whether an imported project looks like a full ZEIA export."""
    scripts = [item for item in manifest.get("scripts") or [] if isinstance(item, dict)]
    objects = [item for item in manifest.get("objects") or [] if isinstance(item, dict)]
    workspaces = [
        item for item in manifest.get("workspaces") or [] if isinstance(item, dict)
    ]
    liquid_class_objects = [
        str(item.get("object_name") or "")
        for item in objects
        if item.get("kind") == "liquid_class" and item.get("object_name")
    ]
    findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not scripts:
        findings.append(
            {
                "id": "no_scripts",
                "summary": "No source scripts were found in this ZEIA/archive.",
            }
        )

    object_names = {
        str(item.get("object_name") or "").casefold()
        for item in [*scripts, *objects]
        if item.get("object_name")
    }
    object_guids = {
        str(guid).casefold()
        for item in [*scripts, *objects]
        for guid in (item.get("guids") or [])
        if guid
    }
    workspace_names = {
        str(item.get("object_name") or "").casefold()
        for item in workspaces
        if item.get("object_name")
    }
    workspace_guids = {
        str(guid).casefold()
        for item in workspaces
        for guid in (item.get("guids") or [])
        if guid
    }
    liquid_class_names = {name.casefold() for name in liquid_class_objects}

    unresolved_refs = []
    missing_worktables = []
    missing_liquid_classes = []
    for script in scripts:
        script_name = (
            script.get("object_name") or script.get("entry") or "unknown script"
        )
        for ref in script.get("references") or []:
            if not isinstance(ref, dict):
                continue
            ref_name = str(ref.get("object_name") or "").strip()
            ref_guid = str(ref.get("guid") or "").strip()
            ref_type = str(ref.get("type_id") or "").strip()
            if ref_type == "WorktableWorkspace":
                if not _ref_resolves(
                    ref_name, ref_guid, workspace_names, workspace_guids
                ):
                    missing_worktables.append(
                        {
                            "script": script_name,
                            "object_name": ref_name,
                            "guid": ref_guid,
                            "type_id": ref_type,
                        }
                    )
                continue
            if ref_name or ref_guid:
                if not _ref_resolves(ref_name, ref_guid, object_names, object_guids):
                    unresolved_refs.append(
                        {
                            "script": script_name,
                            "object_name": ref_name,
                            "guid": ref_guid,
                            "type_id": ref_type,
                        }
                    )
        deps = script.get("dependencies") or {}
        for liquid_class in deps.get("liquid_classes") or []:
            name = str(liquid_class or "").strip()
            if name and name.casefold() not in liquid_class_names:
                missing_liquid_classes.append({"script": script_name, "name": name})

    if missing_worktables:
        findings.append(
            {
                "id": "missing_referenced_worktables",
                "summary": "One or more script WorktableWorkspace references are absent from the archive.",
                "items": missing_worktables[:50],
            }
        )
    if missing_liquid_classes:
        findings.append(
            {
                "id": "missing_liquid_class_objects",
                "summary": "One or more liquid classes used by scripts are absent as liquid-class objects.",
                "items": missing_liquid_classes[:50],
            }
        )
    if unresolved_refs:
        findings.append(
            {
                "id": "unresolved_script_references",
                "summary": "One or more script references do not resolve to an object in the archive.",
                "items": unresolved_refs[:50],
            }
        )
    if scripts and len(objects) < 2:
        findings.append(
            {
                "id": "low_supporting_object_count",
                "summary": "The archive contains scripts but very few supporting objects, which is typical of a non-full export.",
                "details": {
                    "object_count": len(objects),
                    "entry_count": manifest.get("entry_count", 0),
                },
            }
        )
    if scripts and not workspaces:
        findings.append(
            {
                "id": "no_worktable_objects",
                "summary": "No worktable/workspace objects were found beside the source scripts.",
            }
        )
    if scripts and not liquid_class_objects:
        findings.append(
            {
                "id": "no_liquid_class_objects",
                "summary": "No liquid-class objects were found beside the source scripts.",
            }
        )

    completeness = manifest.get("inspection_completeness")
    if isinstance(completeness, dict) and not completeness.get("complete", False):
        findings.append(
            {
                "id": "incomplete_canonical_ingestion",
                "summary": (
                    "Canonical ZEIA inspection is incomplete; full-export readiness "
                    "cannot be established from a bounded preview or failed member parse."
                ),
                "details": {
                    "mode": completeness.get("mode"),
                    "configured_limits": completeness.get("configured_limits") or {},
                    "truncated_scripts": completeness.get("truncated_scripts", 0),
                    "truncated_objects": completeness.get("truncated_objects", 0),
                    "blocking_error_count": completeness.get("blocking_error_count", 0),
                    "oversized_member_count": completeness.get("oversized_member_count", 0),
                    "oversized_members": completeness.get("oversized_members", [])[:50],
                },
            }
        )

    warning_eligible_findings = [
        finding
        for finding in findings
        if str(finding.get("id") or "") in FULL_ZEIA_WARNING_ONLY_FINDING_IDS
    ]
    blocking_findings = [
        finding
        for finding in findings
        if str(finding.get("id") or "") not in FULL_ZEIA_WARNING_ONLY_FINDING_IDS
    ]
    if warning_eligible_findings and _has_dependency_rich_full_export_evidence(
        manifest,
        scripts=scripts,
        objects=objects,
        workspaces=workspaces,
        liquid_class_objects=liquid_class_objects,
    ):
        warnings.extend(
            _full_zeia_warning_record(finding) for finding in warning_eligible_findings
        )
    else:
        blocking_findings.extend(warning_eligible_findings)

    for error in manifest.get("errors") or []:
        if not isinstance(error, dict) or error.get("severity") != "warning":
            continue
        warnings.append(
            {
                "id": "non_semantic_member_diagnostic",
                "summary": (
                    "A known non-semantic archive member was not fully interpreted; "
                    "it was retained as an explicit warning."
                ),
                "items": [error],
            }
        )

    if not blocking_findings:
        status = "likely_full_export"
        summary = (
            "The ZEIA has full-system export evidence; stale references in unrelated scripts "
            "were retained as warnings."
            if warnings
            else "The ZEIA includes scripts plus the referenced worktable and liquid-class dependencies detected in the manifest."
        )
    else:
        status = "needs_user"
        summary = "The ZEIA looks partial/non-full or lacks enough dependency evidence for protocol generation."

    signals = {
        "entry_count": manifest.get("entry_count", 0),
        "script_count": len(scripts),
        "object_count": len(objects),
        "workspace_count": len(workspaces),
        "liquid_class_object_count": len(liquid_class_objects),
    }
    if isinstance(completeness, dict):
        signals.update(
            {
                "inspection_complete": bool(completeness.get("complete")),
                "truncated_scripts": int(completeness.get("truncated_scripts") or 0),
                "truncated_objects": int(completeness.get("truncated_objects") or 0),
                "oversized_member_count": int(
                    completeness.get("oversized_member_count") or 0
                ),
            }
        )

    return {
        "required": True,
        "status": status,
        "accepted": status == "likely_full_export",
        "summary": summary,
        "ask_user": FULL_ZEIA_ASK,
        "signals": signals,
        "blocking_findings": blocking_findings,
        "warnings": warnings,
    }


def _collection_full_zeia_assessment(contexts: list[ProjectContext]) -> dict[str, Any]:
    source_assessments = []
    blocking = []
    accepted = []
    for ctx in contexts:
        assessment = ctx.manifest.get("full_zeia_export") or assess_full_zeia_export(
            ctx.manifest
        )
        source_record = {
            "source_context": ctx.name,
            "status": assessment.get("status"),
            "accepted": bool(assessment.get("accepted")),
            "summary": assessment.get("summary"),
            "blocking_findings": assessment.get("blocking_findings") or [],
        }
        source_assessments.append(source_record)
        if source_record["accepted"]:
            accepted.append(source_record)
        else:
            blocking.append(source_record)
    if blocking:
        if accepted:
            return {
                "required": True,
                "status": "likely_full_export",
                "accepted": True,
                "summary": (
                    "At least one source context looks like a full ZEIA export; "
                    "partial companion contexts were retained as source-specific structure."
                ),
                "ask_user": FULL_ZEIA_ASK,
                "source_assessments": source_assessments,
                "blocking_findings": [],
                "warnings": [
                    {
                        "id": "partial_companion_contexts",
                        "summary": (
                            "One or more companion contexts do not look like full ZEIA exports, "
                            "but the collection includes a full export for dependency resolution."
                        ),
                        "items": blocking,
                    }
                ],
            }
        return {
            "required": True,
            "status": "needs_user",
            "accepted": False,
            "summary": "One or more source contexts do not look like full ZEIA exports.",
            "ask_user": FULL_ZEIA_ASK,
            "source_assessments": source_assessments,
            "blocking_findings": blocking,
            "warnings": [],
        }
    return {
        "required": True,
        "status": "likely_full_export",
        "accepted": True,
        "summary": "All source contexts look like full ZEIA exports based on available manifests.",
        "ask_user": FULL_ZEIA_ASK,
        "source_assessments": source_assessments,
        "blocking_findings": [],
        "warnings": [],
    }


def _ref_resolves(
    name: str,
    guid: str,
    object_names: set[str],
    object_guids: set[str],
) -> bool:
    return bool(
        (name and name.casefold() in object_names)
        or (guid and guid.casefold() in object_guids)
    )


def _has_dependency_rich_full_export_evidence(
    manifest: dict[str, Any],
    *,
    scripts: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    workspaces: list[dict[str, Any]],
    liquid_class_objects: list[str],
) -> bool:
    if not scripts or not workspaces or not liquid_class_objects:
        return False
    supporting_object_floor = max(5, len(scripts))
    return (
        len(objects) >= supporting_object_floor
        and int(manifest.get("entry_count") or 0) >= supporting_object_floor
    )


def _full_zeia_warning_record(finding: dict[str, Any]) -> dict[str, Any]:
    summary = str(finding.get("summary") or "").strip()
    if FULL_ZEIA_WARNING_SUMMARY_SUFFIX not in summary:
        summary = f"{summary} {FULL_ZEIA_WARNING_SUMMARY_SUFFIX}".strip()
    return {**finding, "summary": summary}


def _append_full_zeia_export_report(
    lines: list[str], assessment: dict[str, Any]
) -> None:
    if not assessment:
        return
    lines.extend(
        [
            f"- Full ZEIA export check: `{assessment.get('status') or 'unknown'}`",
            "",
            "## Full ZEIA Export Check",
            "",
            f"- Required: `{bool(assessment.get('required', True))}`",
            f"- Accepted: `{bool(assessment.get('accepted'))}`",
            f"- Summary: {assessment.get('summary') or ''}",
        ]
    )
    if not assessment.get("accepted"):
        lines.append(
            f"- Required user action: {assessment.get('ask_user') or FULL_ZEIA_ASK}"
        )
    signals = assessment.get("signals") or {}
    for key, value in signals.items():
        lines.append(f"- `{key}`: `{value}`")
    for finding in assessment.get("blocking_findings") or []:
        if not isinstance(finding, dict):
            continue
        lines.append(
            f"- Blocking signal `{finding.get('id')}`: {finding.get('summary')}"
        )
    for warning in assessment.get("warnings") or []:
        if not isinstance(warning, dict):
            continue
        lines.append(
            f"- Warning signal `{warning.get('id')}`: {warning.get('summary')}"
        )


def render_project_report(manifest: dict[str, Any]) -> str:
    completeness = manifest.get("inspection_completeness") or {}
    lines = [
        "# Fluent Project Context",
        "",
        f"- Name: `{manifest['name']}`",
        f"- Imported at: `{manifest['imported_at']}`",
        f"- Source archive: `{manifest['source_archive']}`",
        f"- Entries: `{manifest['entry_count']}`",
        f"- Scripts: `{len(manifest.get('scripts', []))}`",
        f"- Objects: `{len(manifest.get('objects', []))}`",
        f"- Workspaces: `{len(manifest.get('workspaces', []))}`",
        f"- Snapshot evidence: `{len(manifest.get('snapshot_evidence', []))}`",
        f"- Canonical inspection complete: `{completeness.get('complete')}`",
        f"- Canonical inspection mode: `{completeness.get('mode') or 'unknown'}`",
        f"- Canonical inspection limits: `{completeness.get('configured_limits') or {}}`",
        f"- Truncated scripts/objects: `{completeness.get('truncated_scripts', 0)}`/`{completeness.get('truncated_objects', 0)}`",
        f"- Oversized XML members: `{completeness.get('oversized_member_count', 0)}`",
    ]
    _append_full_zeia_export_report(lines, manifest.get("full_zeia_export") or {})
    lines.extend(["", "## Extensions", ""])
    for ext, count in manifest.get("extension_counts", {}).items():
        lines.append(f"- `{ext}`: {count}")

    if manifest.get("scripts"):
        lines.extend(["", "## Scripts", ""])
        for script in manifest["scripts"][:30]:
            name = script.get("object_name") or Path(script.get("entry", "")).name
            lines.append(
                f"- `{name}`: `{script.get('entry')}` "
                f"({script.get('command_count', 0)} commands)"
            )

    if manifest.get("workspaces"):
        lines.extend(["", "## Workspaces", ""])
        for workspace in manifest["workspaces"][:30]:
            lines.append(
                f"- `{workspace.get('object_name')}`: `{workspace.get('entry')}`"
            )

    geometry = manifest.get("worktable_geometry") or {}
    if geometry:
        lines.extend(["", "## Worktable Geometry", ""])
        lines.append(f"- Parsed workspaces: `{geometry.get('workspace_count', 0)}`")
        lines.append(
            f"- Parsed components/carriers: `{geometry.get('component_count', 0)}`"
        )
        lines.append(f"- Parsed sites: `{geometry.get('site_count', 0)}`")
        lines.append(f"- Parsed connectors: `{geometry.get('connector_count', 0)}`")
        pin_sites = [
            site.get("pin_name")
            for site in geometry.get("pin_sites", [])
            if site.get("pin_name")
        ]
        if pin_sites:
            lines.append(f"- Worktable pins: `{', '.join(pin_sites[:20])}`")

    _append_snapshot_report(lines, manifest)

    custom_parts = manifest.get("custom_part_summary") or {}
    if custom_parts and custom_parts.get("total_custom_objects"):
        lines.extend(["", "## Custom Parts And Pins", ""])
        lines.append(
            f"- Custom objects/assets: `{custom_parts.get('total_custom_objects', 0)}`"
        )
        lines.append(
            f"- Pin connector objects: `{custom_parts.get('pin_connector_count', 0)}`"
        )
        if custom_parts.get("pin_refs"):
            lines.append(f"- Pin refs: `{', '.join(custom_parts['pin_refs'][:20])}`")
        if custom_parts.get("asset_refs"):
            lines.append(
                f"- Asset refs: `{', '.join(custom_parts['asset_refs'][:20])}`"
            )

    if manifest.get("liquid_classes"):
        lines.extend(["", "## Liquid Classes", ""])
        for name in manifest["liquid_classes"][:50]:
            lines.append(f"- `{name}`")

    if manifest.get("worklist_paths"):
        lines.extend(["", "## Worklists", ""])
        for path in manifest["worklist_paths"][:50]:
            lines.append(f"- `{path}`")

    if manifest.get("catalog_alias_candidates"):
        lines.extend(["", "## Catalog Alias Candidates", ""])
        for alias in manifest["catalog_alias_candidates"][:50]:
            lines.append(f"- `{alias['project_name']}` -> `{alias['base_name']}`")

    if manifest.get("errors"):
        lines.extend(["", "## Import Errors", ""])
        for error in manifest["errors"][:30]:
            lines.append(
                f"- `{error['entry']}` [{error.get('classification', 'unknown')}; "
                f"{error.get('parser', 'unknown')}]: {error['error']}"
            )

    return "\n".join(lines).rstrip() + "\n"


def render_project_collection_report(manifest: dict[str, Any]) -> str:
    completeness = manifest.get("inspection_completeness") or {}
    lines = [
        "# Fluent Project Collection",
        "",
        f"- Name: `{manifest['name']}`",
        f"- Created at: `{manifest['created_at']}`",
        f"- Source projects: `{len(manifest.get('source_projects', []))}`",
        f"- Entries: `{manifest['entry_count']}`",
        f"- Scripts: `{len(manifest.get('scripts', []))}`",
        f"- Objects: `{len(manifest.get('objects', []))}`",
        f"- Workspaces: `{len(manifest.get('workspaces', []))}`",
        f"- Snapshot evidence: `{len(manifest.get('snapshot_evidence', []))}`",
        f"- Canonical inspection complete: `{completeness.get('complete')}`",
        f"- Canonical inspection mode: `{completeness.get('mode') or 'unknown'}`",
        f"- Canonical inspection limits: `{completeness.get('configured_limits') or {}}`",
        f"- Truncated scripts/objects: `{completeness.get('truncated_scripts', 0)}`/`{completeness.get('truncated_objects', 0)}`",
        f"- Oversized XML members: `{completeness.get('oversized_member_count', 0)}`",
    ]
    _append_full_zeia_export_report(lines, manifest.get("full_zeia_export") or {})
    lines.extend(["", "## Source Projects", ""])
    for project in manifest.get("source_projects", []):
        lines.append(
            f"- `{project.get('name')}`: "
            f"scripts={project.get('script_count', 0)}, "
            f"workspaces={project.get('workspace_count', 0)}, "
            f"objects={project.get('object_count', 0)}, "
            f"snapshots={project.get('snapshot_evidence_count', 0)}"
        )

    if manifest.get("scripts"):
        lines.extend(["", "## Scripts", ""])
        for script in manifest["scripts"][:50]:
            name = script.get("object_name") or Path(script.get("entry", "")).name
            source = script.get("source_context") or "unknown"
            lines.append(
                f"- `{source}:{name}`: `{script.get('entry')}` "
                f"({script.get('command_count', 0)} commands)"
            )

    if manifest.get("workspaces"):
        lines.extend(["", "## Workspaces", ""])
        for workspace in manifest["workspaces"][:50]:
            source = workspace.get("source_context") or "unknown"
            lines.append(
                f"- `{source}:{workspace.get('object_name')}`: `{workspace.get('entry')}`"
            )

    geometry = manifest.get("worktable_geometry") or {}
    if geometry:
        lines.extend(["", "## Worktable Geometry", ""])
        lines.append(f"- Parsed workspaces: `{geometry.get('workspace_count', 0)}`")
        lines.append(
            f"- Parsed components/carriers: `{geometry.get('component_count', 0)}`"
        )
        lines.append(f"- Parsed sites: `{geometry.get('site_count', 0)}`")
        lines.append(f"- Parsed connectors: `{geometry.get('connector_count', 0)}`")
        pin_sites = [
            site.get("pin_name")
            for site in geometry.get("pin_sites", [])
            if site.get("pin_name")
        ]
        if pin_sites:
            lines.append(f"- Worktable pins: `{', '.join(pin_sites[:20])}`")

    _append_snapshot_report(lines, manifest)

    custom_parts = manifest.get("custom_part_summary") or {}
    if custom_parts and custom_parts.get("total_custom_objects"):
        lines.extend(["", "## Custom Parts And Pins", ""])
        lines.append(
            f"- Custom objects/assets: `{custom_parts.get('total_custom_objects', 0)}`"
        )
        lines.append(
            f"- Pin connector objects: `{custom_parts.get('pin_connector_count', 0)}`"
        )
        if custom_parts.get("pin_refs"):
            lines.append(f"- Pin refs: `{', '.join(custom_parts['pin_refs'][:20])}`")
        if custom_parts.get("asset_refs"):
            lines.append(
                f"- Asset refs: `{', '.join(custom_parts['asset_refs'][:20])}`"
            )

    if manifest.get("liquid_classes"):
        lines.extend(["", "## Liquid Classes", ""])
        for name in manifest["liquid_classes"][:50]:
            lines.append(f"- `{name}`")

    if manifest.get("errors"):
        lines.extend(["", "## Import Errors", ""])
        for error in manifest["errors"][:50]:
            source = error.get("source_context") or "unknown"
            lines.append(f"- `{source}:{error.get('entry')}`: {error.get('error')}")

    return "\n".join(lines).rstrip() + "\n"


def _collection_item(ctx: ProjectContext, item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    entry = str(out.get("entry") or "")
    object_name = str(out.get("object_name") or "")
    extracted_path = str(out.get("extracted_path") or "")
    if extracted_path:
        out["context_extracted_path"] = extracted_path
        out["extracted_path"] = str((ctx.root / extracted_path).resolve())
    out["source_context"] = ctx.name
    out["context_root"] = str(ctx.root)
    out["context_manifest"] = str(ctx.root / "manifest.json")
    if object_name:
        out["qualified_name"] = f"{ctx.name}:{object_name}"
    if entry:
        out["qualified_entry"] = f"{ctx.name}:{entry}"
    return out


def _should_report_collection_progress(
    completed: int, total: int, *, interval: int
) -> bool:
    return completed == total or (interval > 0 and completed % interval == 0)


def _validate_collection_manifest(
    manifest: dict[str, Any],
    *,
    progress: ProgressEmitter | None = None,
) -> None:
    """Validate collection counts and identities with O(n) indexed lookups."""
    source_projects = manifest.get("source_projects") or []
    source_names = [str(item.get("name") or "") for item in source_projects]
    if len(source_names) != len(set(source_names)):
        raise PipelineError("project collection contains a duplicate source context")

    scripts = manifest.get("scripts") or []
    objects = manifest.get("objects") or []
    worklists = manifest.get("worklists") or []
    expected_scripts = sum(
        int(item.get("script_count") or 0) for item in source_projects
    )
    expected_objects = sum(
        int(item.get("object_count") or 0) for item in source_projects
    )
    expected_worklists = sum(
        int(item.get("worklist_count") or 0) for item in source_projects
    )
    if (
        len(scripts) != expected_scripts
        or len(objects) != expected_objects
        or len(worklists) != expected_worklists
    ):
        raise PipelineError(
            "project collection item counts do not match the loaded source manifests"
        )

    total = len(scripts) + len(objects) + len(worklists)
    identities: set[str] = set()
    for index, item in enumerate(chain(scripts, objects, worklists), start=1):
        source_context = str(item.get("source_context") or "")
        qualified_entry = str(item.get("qualified_entry") or "")
        qualified_name = str(item.get("qualified_name") or "")
        identity = qualified_entry or qualified_name
        if not source_context or not identity:
            raise PipelineError(
                "project collection item is missing a stable source identity"
            )
        if identity in identities:
            raise PipelineError(f"duplicate collection identity: {identity}")
        identities.add(identity)
        if progress is not None and _should_report_collection_progress(
            index,
            total,
            interval=1000,
        ):
            progress.running(
                "validate_collection",
                completed_units=index,
                total_units=total,
                unit_name="entries",
            )


def _dedupe_strings(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _script_resolution_candidates(script: dict[str, Any]) -> set[str]:
    entry = str(script.get("entry") or "")
    object_name = str(script.get("object_name") or "")
    source_context = str(script.get("source_context") or "")
    base = {
        entry,
        object_name,
        Path(entry).name if entry else "",
        Path(entry).stem if entry else "",
        str(script.get("qualified_name") or ""),
        str(script.get("qualified_entry") or ""),
    }
    candidates = {value for value in base if value}
    if source_context:
        for value in list(candidates):
            candidates.add(f"{source_context}:{value}")
            candidates.add(f"{source_context}/{value}")
    return candidates


def _script_resolved_path(ctx: ProjectLike, script: dict[str, Any]) -> Path:
    raw = (
        script.get("resolved_path")
        or script.get("extracted_path")
        or script.get("entry")
        or ""
    )
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (ctx.root / path).resolve()


def resolve_recorded_script_path(
    record: Mapping[str, Any] | None,
    *,
    context: ProjectLike | None = None,
) -> Path | None:
    """Resolve a manifest/IR script record to an on-disk `.xscr` when possible."""
    if not isinstance(record, Mapping):
        return None

    candidates: list[Path] = []
    for key in ("resolved_path", "extracted_path"):
        raw = record.get(key)
        if raw:
            candidates.append(Path(str(raw)).expanduser())

    context_root = str(record.get("context_root") or "").strip()
    if context_root:
        root = Path(context_root)
        for key in ("context_extracted_path", "entry"):
            rel = str(record.get(key) or "").strip()
            if rel:
                candidates.append(root / PurePosixPath(rel.replace("\\", "/")))

    if context is not None:
        for key in ("qualified_name", "object_name"):
            name = str(record.get(key) or "").strip()
            if not name:
                continue
            for candidate_name in {name, name.rsplit(":", 1)[-1]}:
                try:
                    candidates.append(resolve_context_script(context, candidate_name))
                except PipelineError:
                    continue

    seen: set[str] = set()
    for candidate in candidates:
        try:
            path = candidate.resolve()
        except OSError:
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None


def filter_generation_source_script_records(
    records: list[Mapping[str, Any]] | None,
    protocol_ir: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Prefer request-selected source scripts over same-name regeneration baselines."""
    items = [dict(record) for record in records or [] if isinstance(record, Mapping)]
    if not items:
        return []

    source = protocol_ir.get("source") if isinstance(protocol_ir, Mapping) else {}
    requested = {
        str(name).strip().casefold()
        for name in (source.get("source_scripts") or [])
        if str(name).strip()
    }
    if requested:
        filtered = [
            record
            for record in items
            if str(record.get("object_name") or "").strip().casefold() in requested
            or str(record.get("qualified_name") or "")
            .rsplit(":", 1)[-1]
            .strip()
            .casefold()
            in requested
        ]
        if filtered:
            return filtered

    protocol_name = ""
    if isinstance(protocol_ir, Mapping):
        protocol = protocol_ir.get("protocol")
        if isinstance(protocol, Mapping):
            protocol_name = str(protocol.get("name") or "").strip().casefold()
    if protocol_name:
        filtered = [
            record
            for record in items
            if str(record.get("object_name") or "").strip().casefold() != protocol_name
        ]
        if filtered:
            return filtered
    return items


def subroutine_simulate_cli_args(ctx: ProjectLike | None) -> list[str]:
    """Return fluentcoder simulate CLI args that register project subroutines."""
    if ctx is None:
        return []

    extracted = ctx.extracted_dir
    if extracted.is_dir() and any(extracted.rglob("*.xscr")):
        return ["--subroutine-dir", str(extracted)]

    args: list[str] = []
    seen: set[str] = set()
    for script in ctx.manifest.get("scripts") or []:
        if not isinstance(script, dict):
            continue
        path = _script_resolved_path(ctx, script)
        if path.suffix.lower() != ".xscr" or not path.exists():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        args.extend(["--subroutine-xscr", key])
    return args


def _qualified_script_label(script: dict[str, Any]) -> str:
    source = script.get("source_context")
    name = script.get("object_name") or Path(str(script.get("entry") or "")).name
    if source:
        return f"{source}:{name}"
    return str(name or script.get("entry") or "<unknown>")


def _write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    from .driver_macros_export import write_driver_macros_for_context
    from .script_folder_bindings_export import (
        attach_script_folder_bindings,
        write_script_folder_bindings_for_context,
    )

    # Attach ZEIA script→worktable bindings before persist so init preference can
    # read them from the written manifest without a sidecar round-trip.
    attach_script_folder_bindings(manifest)
    _project_store().write_json(root / "manifest.json", manifest)
    geometry = manifest.get("worktable_geometry")
    # Large ZEIA imports skip detailed geometry; still walk Components/*.xcmp for
    # labware_catalog / site dims (same DataStore pattern as connector_graph).
    from .connector_coverage_export import write_connector_coverage_for_context
    from .labware_catalog_export import write_labware_catalog_for_context

    write_labware_catalog_for_context(
        root, geometry if isinstance(geometry, dict) else None
    )
    if isinstance(geometry, dict) and geometry.get("components"):
        write_connector_coverage_for_context(root, geometry)
    # Always try Snap graph write: large ZEIA imports skip detailed geometry, but
    # Connectors/*.xcon still live under extracted DataStore — walk those for full edges.
    from .connector_graph_export import write_connector_graph_for_context
    from .liquid_classes_export import write_liquid_classes_for_context

    write_connector_graph_for_context(
        root, geometry if isinstance(geometry, dict) else None
    )
    write_liquid_classes_for_context(root, manifest)
    write_driver_macros_for_context(root, manifest)
    write_script_folder_bindings_for_context(root, manifest)


def _project_store() -> ProjectStore:
    return ProjectStore(active_context_file=ACTIVE_CONTEXT_FILE)


def is_context_archive(path: Path) -> bool:
    """Return true if a ZIP-like file is a supported project or support context."""
    path = Path(path).expanduser()
    if not path.exists() or not zipfile.is_zipfile(path):
        return False
    try:
        detection = probe_zeia(path)
    except (OSError, ValueError, zipfile.BadZipFile):
        return False
    if detection.status == "supported":
        return True
    try:
        with zipfile.ZipFile(path) as zf:
            entries = zf.namelist()
    except zipfile.BadZipFile:
        return False
    # Snapshot/support archives are intentionally outside the ZEIA semantic
    # adapter boundary and remain discoverable for diagnostics.
    return _archive_has_importable_context(entries)


def _archive_has_importable_context(entries: list[str]) -> bool:
    for entry in entries:
        suffix = Path(entry).suffix.lower()
        if suffix in {".xscr", ".gwl"} | XML_OBJECT_EXTS:
            return True
        if _snapshot_roles_for_entry(entry, suffix, ""):
            return True
    return False


def safe_extract_archive(zf: zipfile.ZipFile, destination: Path) -> None:
    """Extract a validated ZIP archive while rejecting path traversal entries."""
    root = destination.resolve()
    for info in zf.infolist():
        if info.is_dir():
            continue
        relative = _zip_entry_to_path(info.filename)
        target = (root / relative).resolve()
        if root not in target.parents and target != root:
            raise PipelineError(f"unsafe archive entry path: {info.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as source, target.open("wb") as dest:
            shutil.copyfileobj(source, dest)


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    """Compatibility wrapper for older internal import call sites."""
    safe_extract_archive(zf, destination)


def _import_snapshot_archives(
    snapshots: list[Path],
    *,
    source_dir: Path,
    extracted_dir: Path,
    entries: list[str],
) -> list[dict[str, Any]]:
    if not snapshots:
        return []
    source_snapshots_dir = source_dir / "snapshots"
    extracted_snapshots_dir = extracted_dir / "snapshots"
    source_snapshots_dir.mkdir(parents=True, exist_ok=True)
    extracted_snapshots_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    used_prefixes: set[str] = set()
    for index, snapshot in enumerate(snapshots, start=1):
        prefix_name = _unique_snapshot_prefix(snapshot, index, used_prefixes)
        copied = source_snapshots_dir / snapshot.name
        if copied.exists():
            copied = (
                source_snapshots_dir
                / f"{prefix_name}{snapshot.suffix.lower() or '.zip'}"
            )
        shutil.copy2(snapshot, copied)
        target_dir = extracted_snapshots_dir / prefix_name
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(snapshot) as zf:
            snapshot_entries = zf.namelist()
            _safe_extract(zf, target_dir)
        prefix = f"snapshots/{prefix_name}"
        entries.extend(f"{prefix}/{entry}" for entry in snapshot_entries)
        records.append(
            {
                "source_archive": str(snapshot),
                "copied_archive": str(copied),
                "extracted_dir": str(target_dir),
                "prefix": prefix,
                "entry_count": len(snapshot_entries),
                "archive_kind": "snapshot"
                if _entries_have_snapshot_hints(snapshot_entries)
                else "zip",
            }
        )
    return records


def _entries_have_snapshot_hints(entries: list[str]) -> bool:
    return any(
        _snapshot_roles_for_entry(entry, Path(entry).suffix.lower(), "")
        for entry in entries
    )


def _unique_snapshot_prefix(snapshot: Path, index: int, used: set[str]) -> str:
    base = sanitize_project_name(snapshot.stem, f"snapshot-{index}")
    candidate = base
    counter = 2
    while candidate in used:
        candidate = f"{base}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _zip_entry_to_path(entry: str) -> Path:
    normalized = entry.replace("\\", "/")
    parts = [part for part in PurePosixPath(normalized).parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise PipelineError(f"unsafe archive entry path: {entry}")
    return Path(*parts)


def _remove_project_dir(root: Path) -> None:
    projects_root = PROJECTS_DIR.resolve()
    resolved = root.resolve()
    if projects_root not in resolved.parents:
        raise PipelineError(
            f"refusing to remove path outside ready-to-import directory: {resolved}"
        )
    shutil.rmtree(resolved)


def _remove_collection_dir(root: Path) -> None:
    collections_root = COLLECTIONS_DIR.resolve()
    resolved = root.resolve()
    if collections_root not in resolved.parents:
        raise PipelineError(
            f"refusing to remove path outside ready-to-import directory: {resolved}"
        )
    shutil.rmtree(resolved)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")



def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _inspect_snapshot_evidence(
    path: Path, entry: str, relative: str, suffix: str
) -> dict[str, Any] | None:
    size = path.stat().st_size if path.exists() else 0
    text = ""
    text_error = ""
    if _should_read_snapshot_text(suffix, size):
        try:
            text = _read_text(path)
        except Exception as exc:  # pragma: no cover - platform codecs vary
            text_error = str(exc)

    roles = _snapshot_roles_for_entry(entry, suffix, text)
    if not roles:
        return None

    fields = _snapshot_extracted_fields(text)
    record: dict[str, Any] = {
        "kind": "snapshot_evidence",
        "entry": entry,
        "extracted_path": relative,
        "object_name": Path(entry.replace("\\", "/")).name,
        "extension": suffix or "<none>",
        "size_bytes": size,
        "category": roles[0],
        "roles": roles,
        "signals": _snapshot_signals(entry, suffix, text, roles),
        "summary": _snapshot_record_summary(entry, roles, fields),
    }
    if fields:
        record["extracted_fields"] = fields
    if text_error:
        record["text_error"] = text_error
    return record


def _should_inspect_snapshot_evidence(entry: str, suffix: str) -> bool:
    normalized = entry.replace("\\", "/").casefold()
    if normalized.startswith("snapshots/"):
        return True
    if suffix.lower() in SNAPSHOT_BINARY_EXTS:
        return True
    if suffix.lower() not in SNAPSHOT_TEXT_EXTS:
        return False
    basename = Path(normalized).name
    return any(
        token in normalized or token in basename
        for token in (
            "snapshot",
            "instrument",
            "configuration",
            "firmware",
            "driver",
            "fluentcontrol",
            "tecan",
            "worktable",
            "system",
            "log",
        )
    )


def _should_read_snapshot_text(suffix: str, size: int) -> bool:
    return suffix.lower() in SNAPSHOT_TEXT_EXTS and size <= SNAPSHOT_TEXT_MAX_BYTES


def _snapshot_roles_for_entry(entry: str, suffix: str, text: str) -> list[str]:
    normalized = entry.replace("\\", "/")
    entry_text = normalized.casefold()
    basename = Path(normalized).name.casefold()
    sample = text[:12000].casefold()
    haystack = f"{entry_text}\n{sample}"
    roles: list[str] = []

    def add(role: str) -> None:
        if role not in roles:
            roles.append(role)

    if basename == "system.config":
        add("instrument_configuration")
        add("simulation_setup")
        add("hardware_details")
    if _has_any(
        haystack,
        [
            "instrument configuration",
            "instrument_configuration",
            "instrumentconfiguration",
            "instrument files",
            "instrumentfiles",
            "instrument serial",
            "instrumentserial",
            "serial number",
            "serialnumber",
        ],
    ) or (suffix in {".cfg", ".config"} and "instrument" in haystack):
        add("instrument_configuration")
    if _has_any(
        haystack,
        ["simulation", "simulator", "demo mode", "system.config", "3d simulator"],
    ):
        add("simulation_setup")
    if suffix == ".reg" or _has_any(
        haystack,
        [
            "firmware",
            "driver configuration",
            "driverconfiguration",
            "driver configurations",
            "assembly list",
            "assemblylist",
            "move counter",
            "move counters",
            "state files",
            "statefiles",
            "registry export",
            "registryexport",
            "system information",
            "systeminformation",
            "arm order",
            "tip configuration",
            "integrated devices",
            "hardware configuration",
        ],
    ):
        add("hardware_details")
    if (
        suffix in SNAPSHOT_BINARY_EXTS
        or suffix == ".log"
        or _has_any(
            haystack,
            [
                "user description",
                "userdescription",
                "issue description",
                "issuedescription",
                "screenshot",
                "screen shot",
                "windows event",
                "event log",
                "audit trail",
                "audittrail",
                "sample tracking",
                "sampletracking",
                "journal",
                "dump file",
                "dumpfile",
                "deckcheck",
                "deck check",
                "log file",
                "logfiles",
                "crash",
                "error",
            ],
        )
    ):
        add("troubleshooting_context")
    if suffix in ASSET_EXTS and _has_any(
        entry_text,
        ["screenshot", "screen shot", "deckcheck", "deck check", "picture", "monitor"],
    ):
        add("troubleshooting_context")
    if "snapshot" in entry_text and suffix in SNAPSHOT_TEXT_EXTS | SNAPSHOT_BINARY_EXTS:
        add("troubleshooting_context")
    return roles


def _has_any(haystack: str, needles: list[str]) -> bool:
    return any(needle in haystack for needle in needles)


def _snapshot_signals(
    entry: str, suffix: str, text: str, roles: list[str]
) -> list[str]:
    normalized = entry.replace("\\", "/")
    basename = Path(normalized).name
    signals = list(roles)
    if basename.casefold() == "system.config":
        signals.append("system.config")
    if suffix:
        signals.append(suffix.lower())
    sample = text[:12000].casefold()
    for label, needles in {
        "serial_number": ["serial number", "serialnumber", "instrument serial"],
        "firmware_version": ["firmware"],
        "driver_version": ["driver version", "driverversion"],
        "arm_order": ["arm order", "armorder"],
        "tip_configuration": ["tip configuration", "tipconfiguration"],
    }.items():
        if _has_any(sample, needles):
            signals.append(label)
    return _dedupe_strings(signals)[:30]


def _snapshot_extracted_fields(text: str) -> dict[str, list[str]]:
    if not text.strip():
        return {}
    fields: dict[str, list[str]] = {}
    _merge_field_values(fields, _snapshot_xml_fields(text))
    _merge_field_values(fields, _snapshot_key_value_fields(text))
    return {key: values[:10] for key, values in sorted(fields.items()) if values}


def _snapshot_xml_fields(text: str) -> dict[str, list[str]]:
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    wanted = {
        "Arm",
        "ArmOrder",
        "ConfigurationName",
        "Device",
        "DeviceAlias",
        "DeviceName",
        "Driver",
        "DriverVersion",
        "Firmware",
        "FirmwareVersion",
        "Instrument",
        "InstrumentName",
        "InstrumentSerialNumber",
        "InstrumentSize",
        "Name",
        "Serial",
        "SerialNumber",
        "SoftwareVersion",
        "TipConfiguration",
        "TipType",
        "Version",
    }
    out = {key: [] for key in wanted}
    for el in root.iter():
        name = _local_name(el.tag)
        if name not in wanted:
            continue
        value = (el.text or "").strip()
        if value:
            out[name].append(value)
        for attr, raw in el.attrib.items():
            attr_name = _local_name(attr)
            if attr_name in wanted and raw.strip():
                out[attr_name].append(raw.strip())
            elif (
                name in {"Arm", "Device", "Driver", "Instrument"}
                and attr_name in {"Name", "Version", "SerialNumber"}
                and raw.strip()
            ):
                out[f"{name}.{attr_name}"] = [
                    *out.get(f"{name}.{attr_name}", []),
                    raw.strip(),
                ]
    return {key: _dedupe_strings(values) for key, values in out.items() if values}


def _snapshot_key_value_fields(text: str) -> dict[str, list[str]]:
    interesting = re.compile(
        r"(arm|configuration|device|driver|firmware|instrument|serial|software|tip|version)",
        flags=re.IGNORECASE,
    )
    out: dict[str, list[str]] = {}
    for line in text.splitlines()[:600]:
        if not line or len(line) > 500:
            continue
        match = re.match(
            r"\s*([A-Za-z][A-Za-z0-9_. /-]{1,80})\s*[:=]\s*(.+?)\s*$", line
        )
        if not match:
            continue
        key = re.sub(r"\s+", " ", match.group(1).strip())
        value = match.group(2).strip().strip('"')
        if not key or not value or not interesting.search(key):
            continue
        values = out.setdefault(key, [])
        if value not in values:
            values.append(value)
    return out


def _merge_field_values(
    target: dict[str, list[str]], source: dict[str, list[str]]
) -> None:
    for key, values in source.items():
        current = target.setdefault(key, [])
        for value in values:
            if value not in current:
                current.append(value)


def _snapshot_record_summary(
    entry: str, roles: list[str], fields: dict[str, list[str]]
) -> str:
    bits = ["/".join(roles)]
    serials = _values_for_field_names(
        fields, {"serial", "serialnumber", "instrumentserialnumber"}
    )
    versions = _values_for_field_names(
        fields, {"firmwareversion", "softwareversion", "driverversion", "version"}
    )
    if serials:
        bits.append(f"serial={serials[0]}")
    if versions:
        bits.append(f"version={versions[0]}")
    normalized = entry.replace("\\", "/")
    return f"{Path(normalized).name}: " + ", ".join(bits)


def _alias_candidates(names: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name in sorted(set(names)):
        match = re.match(r"^(.+?)\[\d+\]$", name)
        if match:
            out.append({"project_name": name, "base_name": match.group(1).strip()})
    return out


def _custom_part_summary(
    objects: list[dict[str, Any]], scripts: list[dict[str, Any]]
) -> dict[str, Any]:
    counters: Counter[str] = Counter()
    pin_refs: set[str] = set()
    asset_refs: set[str] = set()
    for obj in objects:
        _update_custom_part_rollup(counters, pin_refs, asset_refs, obj)
    for script in scripts:
        deps = script.get("dependencies") or {}
        if deps.get("pin_refs") or deps.get("worktable_pin_locations"):
            counters["script_pin_refs"] += 1
        if deps.get("custom_asset_refs"):
            counters["script_asset_refs"] += 1
        pin_refs.update(str(value) for value in deps.get("pin_refs") or [] if value)
        pin_refs.update(
            str(value) for value in deps.get("worktable_pin_locations") or [] if value
        )
        asset_refs.update(
            str(value) for value in deps.get("custom_asset_refs") or [] if value
        )
    return _custom_part_summary_payload(counters, pin_refs, asset_refs)


def _update_custom_part_rollup(
    counters: Counter[str],
    pin_refs: set[str],
    asset_refs: set[str],
    item: dict[str, Any],
) -> None:
    if item.get("custom_part"):
        counters["total_custom_objects"] += 1
    if item.get("kind") == "connector" and item.get("pin_refs"):
        counters["pin_connector_count"] += 1
    if item.get("kind") == "asset":
        counters["asset_count"] += 1
    pin_refs.update(str(value) for value in item.get("pin_refs") or [] if value)
    asset_refs.update(str(value) for value in item.get("asset_refs") or [] if value)


def _custom_part_summary_payload(
    counters: Counter[str],
    pin_refs: set[str],
    asset_refs: set[str],
) -> dict[str, Any]:
    return {
        "total_custom_objects": int(counters.get("total_custom_objects", 0)),
        "pin_connector_count": int(counters.get("pin_connector_count", 0)),
        "asset_count": int(counters.get("asset_count", 0)),
        "script_pin_ref_count": int(counters.get("script_pin_refs", 0)),
        "script_asset_ref_count": int(counters.get("script_asset_refs", 0)),
        "pin_refs": sorted(pin_refs)[:100],
        "asset_refs": sorted(asset_refs)[:100],
    }


def _archive_kind(
    scripts: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    worklists: list[dict[str, Any]],
    snapshot_evidence: list[dict[str, Any]],
) -> str:
    if snapshot_evidence and scripts:
        return "project_with_snapshot"
    if snapshot_evidence and not scripts:
        return "snapshot"
    if scripts or objects or worklists:
        return "project"
    return "archive"


def _snapshot_summary(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    role_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    paths_by_role: dict[str, list[str]] = {
        "instrument_configuration": [],
        "simulation_setup": [],
        "hardware_details": [],
        "troubleshooting_context": [],
    }
    fields: dict[str, list[str]] = {}
    system_config_paths: list[str] = []
    log_count = 0
    screenshot_count = 0
    dump_count = 0
    for item in evidence:
        entry = str(item.get("entry") or "")
        suffix = str(item.get("extension") or "").lower()
        extension_counts[suffix or "<none>"] += 1
        roles = [str(role) for role in item.get("roles") or [] if role]
        for role in roles:
            role_counts[role] += 1
            if role in paths_by_role and entry not in paths_by_role[role]:
                paths_by_role[role].append(entry)
        if Path(entry.replace("\\", "/")).name.casefold() == "system.config":
            system_config_paths.append(entry)
        if suffix == ".log":
            log_count += 1
        if suffix in ASSET_EXTS and "troubleshooting_context" in roles:
            screenshot_count += 1
        if suffix in {".dmp", ".dump"}:
            dump_count += 1
        extracted_fields = item.get("extracted_fields") or {}
        if isinstance(extracted_fields, dict):
            _merge_field_values(
                fields,
                {
                    str(key): [str(value) for value in values]
                    for key, values in extracted_fields.items()
                    if isinstance(values, list)
                },
            )

    return {
        "status": "snapshot_evidence_found" if evidence else "no_snapshot_evidence",
        "evidence_count": len(evidence),
        "role_counts": dict(sorted(role_counts.items())),
        "extension_counts": dict(sorted(extension_counts.items())),
        "instrument_configuration_files": paths_by_role["instrument_configuration"][
            :50
        ],
        "simulation_setup_files": paths_by_role["simulation_setup"][:50],
        "hardware_detail_files": paths_by_role["hardware_details"][:50],
        "troubleshooting_context_files": paths_by_role["troubleshooting_context"][:50],
        "system_config_paths": _dedupe_strings(system_config_paths)[:20],
        "instrument_serial_numbers": _values_for_field_names(
            fields, {"serial", "serialnumber", "instrumentserialnumber"}
        )[:20],
        "software_versions": _values_for_field_names(
            fields, {"softwareversion", "version"}
        )[:20],
        "firmware_versions": _values_for_field_names(
            fields, {"firmware", "firmwareversion"}
        )[:20],
        "driver_versions": _values_for_field_names(fields, {"driver", "driverversion"})[
            :20
        ],
        "log_file_count": log_count,
        "screenshot_file_count": screenshot_count,
        "dump_file_count": dump_count,
        "notes": [
            "Snapshot evidence is static support context; verify the selected instrument configuration in FluentControl before simulation or real runs.",
        ],
    }


def _values_for_field_names(fields: dict[str, list[str]], names: set[str]) -> list[str]:
    wanted = {_normalize_field_name(name) for name in names}
    out: list[str] = []
    for key, values in fields.items():
        normalized = _normalize_field_name(key)
        if normalized not in wanted and not any(name in normalized for name in wanted):
            continue
        for value in values:
            if value and value not in out:
                out.append(value)
    return out


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _append_snapshot_report(lines: list[str], manifest: dict[str, Any]) -> None:
    evidence = manifest.get("snapshot_evidence") or []
    if not evidence:
        return
    summary = manifest.get("snapshot_summary") or {}
    role_counts = summary.get("role_counts") or {}
    lines.extend(["", "## Snapshot Evidence", ""])
    lines.append(f"- Evidence files: `{len(evidence)}`")
    for role in (
        "instrument_configuration",
        "simulation_setup",
        "hardware_details",
        "troubleshooting_context",
    ):
        if role_counts.get(role):
            label = role.replace("_", " ")
            lines.append(f"- {label.title()}: `{role_counts[role]}`")
    if summary.get("system_config_paths"):
        lines.append(
            f"- system.config files: `{', '.join(summary['system_config_paths'][:10])}`"
        )
    if summary.get("instrument_serial_numbers"):
        lines.append(
            f"- Instrument serials: `{', '.join(summary['instrument_serial_numbers'][:10])}`"
        )
    if summary.get("firmware_versions"):
        lines.append(
            f"- Firmware versions: `{', '.join(summary['firmware_versions'][:10])}`"
        )
    if summary.get("driver_versions"):
        lines.append(
            f"- Driver versions: `{', '.join(summary['driver_versions'][:10])}`"
        )
    for item in evidence[:30]:
        roles = ", ".join(item.get("roles") or [])
        lines.append(f"- `{item.get('object_name')}`: `{item.get('entry')}` ({roles})")
