"""Deterministic full-export readiness resolution.

The project reader owns this decision.  Downstream protocol-builder surfaces
consume the serialized result instead of independently guessing whether a ZEIA
contains the dependencies requested by its scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .project_index import discover_zeia_paths
from .project_model import CanonicalProjectModel
from .zeia_adapters import ZeiaFormatError, ingest_zeia


class ReadinessStatus(str, Enum):
    """Factual status of an export, independent of user approval."""

    COMPLETE = "complete"
    COMPLETE_WITH_WARNINGS = "complete_with_warnings"
    PARTIAL = "partial"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class UnresolvedReference:
    """A required reference that could not be resolved unambiguously."""

    source_archive: str
    source_entry: str
    source_entity: str
    referenced_target: str
    target_type: str
    candidates: tuple[dict[str, Any], ...] = ()
    diagnostic_code: str = "unresolved_reference"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_archive": self.source_archive,
            "source_entry": self.source_entry,
            "source_entity": self.source_entity,
            "referenced_target": self.referenced_target,
            "target_type": self.target_type,
            "candidates": [dict(item) for item in self.candidates],
            "diagnostic_code": self.diagnostic_code,
        }


@dataclass(frozen=True)
class IdentifierConflict:
    """Multiple archive entities claim the same identifier."""

    identifier: str
    identifier_type: str
    candidates: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "identifier_type": self.identifier_type,
            "candidates": [dict(item) for item in self.candidates],
        }


@dataclass(frozen=True)
class FullExportReadiness:
    """Stable, JSON-compatible readiness result for one or more archives."""

    status: ReadinessStatus
    archives: tuple[str, ...] = ()
    unresolved_references: tuple[UnresolvedReference, ...] = ()
    conflicts: tuple[IdentifierConflict, ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    signals: dict[str, Any] = field(default_factory=dict)
    approved_partial: bool = False

    @property
    def accepted(self) -> bool:
        return self.status in {
            ReadinessStatus.COMPLETE,
            ReadinessStatus.COMPLETE_WITH_WARNINGS,
        } or (self.status == ReadinessStatus.PARTIAL and self.approved_partial)

    def to_dict(self) -> dict[str, Any]:
        unresolved = [item.to_dict() for item in self.unresolved_references]
        conflicts = [item.to_dict() for item in self.conflicts]
        warnings = [dict(item) for item in self.warnings]
        diagnostics = [dict(item) for item in self.diagnostics]
        return {
            "schema_version": "tecan.full_export_readiness.v1",
            "status": self.status.value,
            "archives": list(self.archives),
            "accepted": self.accepted,
            "approved_partial_zeia": self.approved_partial,
            "unresolved_references": unresolved,
            "conflicts": conflicts,
            "warnings": warnings,
            "diagnostics": diagnostics,
            "signals": dict(self.signals),
        }


def resolve_full_export_readiness(
    inputs: CanonicalProjectModel | Mapping[str, Any] | str | Path | Iterable[str | Path | CanonicalProjectModel | Mapping[str, Any]],
    *,
    approve_partial_zeia: bool = False,
) -> FullExportReadiness:
    """Resolve one explicit archive, archive directory, or canonical model set.

    Paths are discovered only through :func:`discover_zeia_paths`; models and
    manifests are accepted for consumers that already performed canonical
    ingestion.  No ZIP members are reparsed by this resolver.
    """

    models, path_diagnostics = _models_from_inputs(inputs)
    if path_diagnostics:
        status = _diagnostic_status(path_diagnostics)
        return FullExportReadiness(
            status=status,
            diagnostics=tuple(path_diagnostics),
            approved_partial=approve_partial_zeia,
        )
    return _resolve_models(models, approve_partial_zeia=approve_partial_zeia)


def resolve_manifest_readiness(
    manifest: Mapping[str, Any], *, approve_partial_zeia: bool = False
) -> dict[str, Any]:
    """Resolve a project/collection manifest without creating a second heuristic."""
    # A collection may contain compact context manifests but no model payload.
    source_manifests = manifest.get("source_manifests")
    if isinstance(source_manifests, list):
        nested = [
            item
            for item in source_manifests
            if isinstance(item, Mapping)
        ]
        if nested:
            return _compatibility_view(
                _resolve_models(
                    [model for item in nested for model in _model_from_manifest(item)],
                    approve_partial_zeia=approve_partial_zeia,
                )
            )
    return _compatibility_view(
        _resolve_records(
            source_archive=str(manifest.get("source_archive") or ""),
            scripts=manifest.get("scripts") or [],
            objects=manifest.get("objects") or [],
            worklists=manifest.get("worklists") or [],
            errors=manifest.get("errors") or [],
            completeness=manifest.get("inspection_completeness") or {},
            approve_partial_zeia=approve_partial_zeia,
        )
    )


def _models_from_inputs(
    inputs: CanonicalProjectModel | Mapping[str, Any] | str | Path | Iterable[str | Path | CanonicalProjectModel | Mapping[str, Any]],
) -> tuple[list[CanonicalProjectModel], list[dict[str, Any]]]:
    if isinstance(inputs, (CanonicalProjectModel, Mapping, str, Path)):
        values = [inputs]
    else:
        values = list(inputs)
    paths: list[Path] = []
    models: list[CanonicalProjectModel] = []
    diagnostics: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, CanonicalProjectModel):
            models.append(value)
        elif isinstance(value, Mapping):
            models.extend(_model_from_manifest(value))
        else:
            paths.append(Path(value))
    if paths:
        try:
            discovered = discover_zeia_paths(paths)
        except (FileNotFoundError, OSError, ValueError) as exc:
            return [], [{"code": "unsupported_export", "message": str(exc)}]
        for path in discovered:
            try:
                models.append(ingest_zeia(path))
            except ZeiaFormatError as exc:
                diagnostics.append(
                    {
                        "code": f"{exc.result.status}_export",
                        "archive": str(path.resolve()),
                        "message": str(exc),
                        "detection": exc.result.to_dict(),
                    }
                )
            except Exception as exc:  # parser errors are factual corruption
                diagnostics.append(
                    {
                        "code": "corrupt_export",
                        "archive": str(path.resolve()),
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
    return models, diagnostics


def _model_from_manifest(manifest: Mapping[str, Any]) -> list[CanonicalProjectModel]:
    payload = manifest.get("canonical_model")
    if isinstance(payload, Mapping):
        # Context manifests intentionally store compact model metadata only; the
        # full records below remain the canonical input for this compatibility
        # path and avoid reparsing the source ZIP.
        payload = dict(payload)
    scripts = manifest.get("scripts")
    objects = manifest.get("objects")
    worklists = manifest.get("worklists")
    if not any(isinstance(value, list) for value in (scripts, objects, worklists)):
        return []
    return [
        CanonicalProjectModel.from_inspection(
            {
                "scripts": scripts or [],
                "objects": objects or [],
                "gwls": worklists or [],
                "errors": manifest.get("errors") or [],
                "entry_count": manifest.get("entry_count", 0),
                "script_count_total": manifest.get("script_count_total", len(scripts or [])),
                "completeness": manifest.get("inspection_completeness") or {},
            },
            source_archive=manifest.get("source_archive") or "<manifest>",
            adapter_id=str((payload or {}).get("adapter_id") or "manifest"),
            detection=(payload or {}).get("detection") or {},
        )
    ]


def _resolve_models(
    models: Sequence[CanonicalProjectModel], *, approve_partial_zeia: bool
) -> FullExportReadiness:
    if not models:
        return FullExportReadiness(
            status=ReadinessStatus.PARTIAL,
            diagnostics=(
                {"code": "no_scripts", "message": "No source scripts were found."},
            ),
            approved_partial=approve_partial_zeia,
        )
    return _resolve_records(
        source_archive="",
        scripts=[item for model in models for item in model.scripts],
        objects=[item for model in models for item in model.objects],
        worklists=[item for model in models for item in model.worklists],
        errors=[item for model in models for item in model.errors],
        completeness=_combined_completeness(models),
        archives=tuple(sorted(str(Path(model.source_archive).resolve()) for model in models)),
        approve_partial_zeia=approve_partial_zeia,
    )


def _resolve_records(
    *,
    source_archive: str,
    scripts: Sequence[Mapping[str, Any]],
    objects: Sequence[Mapping[str, Any]],
    worklists: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    completeness: Mapping[str, Any],
    approve_partial_zeia: bool,
    archives: Sequence[str] = (),
) -> FullExportReadiness:
    scripts = [dict(item) for item in scripts if isinstance(item, Mapping)]
    objects = [dict(item) for item in objects if isinstance(item, Mapping)]
    worklists = [dict(item) for item in worklists if isinstance(item, Mapping)]
    errors = [dict(item) for item in errors if isinstance(item, Mapping)]
    record_archives = {
        str((item.get("provenance") or {}).get("source_archive") or "")
        for item in [*scripts, *objects, *worklists]
    }
    all_archives = tuple(
        sorted((set(archives) | record_archives | {str(source_archive)}) - {""})
    )
    conflicts = _find_conflicts([*scripts, *objects, *worklists])
    unresolved = _find_unresolved(scripts, objects, worklists)
    warnings = [
        error
        for error in errors
        if str(error.get("severity") or "error").casefold() == "warning"
    ]
    blocking_errors = [
        error
        for error in errors
        if str(error.get("severity") or "error").casefold() != "warning"
    ]
    workspaces = [item for item in objects if str(item.get("kind") or "").casefold() == "workspace"]
    liquid_classes = [item for item in objects if str(item.get("kind") or "").casefold() == "liquid_class"]
    structural: list[dict[str, Any]] = []
    if not scripts:
        structural.append({"code": "no_scripts", "message": "No source scripts were found."})
    if scripts and len(objects) < 2:
        structural.append(
            {
                "code": "low_supporting_object_count",
                "message": "The archive contains scripts but too few supporting objects.",
                "object_count": len(objects),
            }
        )
    if scripts and not workspaces:
        structural.append(
            {"code": "no_worktable_objects", "message": "No worktable/workspace objects were found."}
        )
    if scripts and not liquid_classes:
        structural.append(
            {"code": "no_liquid_class_objects", "message": "No liquid-class objects were found."}
        )
    incomplete = not bool(completeness.get("complete", True))
    if incomplete:
        structural.append(
            {
                "id": "incomplete_canonical_ingestion",
                "code": "incomplete_canonical_ingestion",
                "message": "Canonical ZEIA ingestion did not inspect every required member.",
                "details": {
                    "mode": completeness.get("mode"),
                    "truncated_scripts": completeness.get("truncated_scripts", 0),
                    "truncated_objects": completeness.get("truncated_objects", 0),
                    "blocking_error_count": completeness.get("blocking_error_count", 0),
                },
            }
        )
    if blocking_errors:
        status = ReadinessStatus.CORRUPT
    elif conflicts:
        status = ReadinessStatus.AMBIGUOUS
    elif structural:
        status = ReadinessStatus.PARTIAL
    elif incomplete:
        status = ReadinessStatus.PARTIAL
    elif unresolved:
        status = ReadinessStatus.PARTIAL
    elif warnings:
        status = ReadinessStatus.COMPLETE_WITH_WARNINGS
    elif not scripts:
        status = ReadinessStatus.PARTIAL
    else:
        status = ReadinessStatus.COMPLETE
    diagnostics = tuple(
        {
            "code": "parse_error",
            "message": str(error.get("error") or error.get("message") or "member parse failed"),
            "entry": error.get("entry"),
            "classification": error.get("classification"),
        }
        for error in blocking_errors
    ) + tuple(structural)
    return FullExportReadiness(
        status=status,
        archives=all_archives,
        unresolved_references=tuple(unresolved),
        conflicts=tuple(conflicts),
        warnings=tuple(warnings),
        diagnostics=diagnostics,
        signals={
            "script_count": len(scripts),
            "object_count": len(objects),
            "worklist_count": len(worklists),
            "archive_count": len(all_archives),
            "inspection_complete": bool(completeness.get("complete", True)),
        },
        approved_partial=approve_partial_zeia,
    )


def _combined_completeness(models: Sequence[CanonicalProjectModel]) -> dict[str, Any]:
    values = [model.completeness or model.source_metadata.get("completeness") or {} for model in models]
    return {
        "complete": all(bool(value.get("complete", False)) for value in values),
        "mode": "complete" if all(value.get("mode") == "complete" for value in values) else "preview",
        "truncated_scripts": sum(int(value.get("truncated_scripts") or 0) for value in values),
        "truncated_objects": sum(int(value.get("truncated_objects") or 0) for value in values),
        "blocking_error_count": sum(int(value.get("blocking_error_count") or 0) for value in values),
    }


def _find_conflicts(records: Sequence[Mapping[str, Any]]) -> list[IdentifierConflict]:
    identifiers: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        provenance = record.get("provenance") or {}
        source = {
            "source_archive": provenance.get("source_archive") or "",
            "source_entry": provenance.get("entry_path") or record.get("entry") or "",
            "kind": record.get("kind") or "object",
            "object_name": record.get("object_name") or "",
            "fingerprint": _record_fingerprint(record),
        }
        names = [record.get("object_name"), *(record.get("names") or [])]
        for name in names:
            if name:
                identifiers.setdefault(("name", str(name).casefold()), []).append(source)
        for guid in record.get("guids") or []:
            if guid:
                identifiers.setdefault(("guid", str(guid).casefold()), []).append(source)
    conflicts = []
    for (identifier_type, identifier), candidates in sorted(identifiers.items()):
        unique = {item["fingerprint"] for item in candidates}
        if len(unique) > 1:
            conflicts.append(
                IdentifierConflict(identifier, identifier_type, tuple(sorted(candidates, key=lambda item: tuple(str(item.get(key) or "") for key in ("source_archive", "source_entry", "kind")))))
            )
    return conflicts


def _record_fingerprint(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Compare definitions, not provenance, when archives repeat shared objects."""
    return (
        str(record.get("kind") or "").casefold(),
        str(record.get("object_name") or "").casefold(),
        str(record.get("type_id") or "").casefold(),
        ",".join(sorted(str(value).casefold() for value in record.get("guids") or [])),
        ",".join(sorted(str(value).casefold() for value in record.get("names") or [])),
        str(record.get("functional_group") or "").casefold(),
    )


def _find_unresolved(
    scripts: Sequence[Mapping[str, Any]],
    objects: Sequence[Mapping[str, Any]],
    worklists: Sequence[Mapping[str, Any]],
) -> list[UnresolvedReference]:
    records = [*objects, *scripts, *worklists]
    name_index: dict[str, list[Mapping[str, Any]]] = {}
    guid_index: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        for name in [record.get("object_name"), *(record.get("names") or [])]:
            if name:
                name_index.setdefault(str(name).casefold(), []).append(record)
        for guid in [*(record.get("guids") or []), record.get("guid")]:
            if guid:
                guid_index.setdefault(str(guid).casefold(), []).append(record)
    unresolved: list[UnresolvedReference] = []
    for script in scripts:
        provenance = script.get("provenance") or {}
        archive = str(provenance.get("source_archive") or "")
        entry = str(provenance.get("entry_path") or script.get("entry") or "")
        entity = str(script.get("object_name") or entry or "unknown script")
        for ref in script.get("references") or []:
            if not isinstance(ref, Mapping):
                continue
            name = str(ref.get("object_name") or "").strip()
            guid = str(ref.get("guid") or "").strip()
            target_type = str(ref.get("type_id") or "reference")
            candidates = _lookup(name, guid, name_index, guid_index)
            if len(candidates) != 1 or not _kind_matches(candidates, target_type):
                unresolved.append(
                    UnresolvedReference(
                        archive,
                        entry,
                        entity,
                        name or guid or "<unnamed reference>",
                        target_type,
                        tuple(_candidate(item) for item in candidates),
                        "ambiguous_reference" if len(candidates) > 1 else "unresolved_reference",
                    )
                )
        dependencies = script.get("dependencies") or {}
        for key, values in dependencies.items():
            if not isinstance(values, (list, tuple)):
                continue
            if key not in {"liquid_classes", "worktables", "subroutines", "worklists", "custom_asset_refs"}:
                continue
            for value in values:
                needle = str(value or "").strip()
                if not needle:
                    continue
                candidates = _lookup(needle, "", name_index, guid_index)
                if len(candidates) != 1:
                    unresolved.append(
                        UnresolvedReference(
                            archive, entry, entity, needle, key,
                            tuple(_candidate(item) for item in candidates),
                            "ambiguous_dependency" if len(candidates) > 1 else "unresolved_dependency",
                        )
                    )
    return sorted(unresolved, key=lambda item: (item.source_archive, item.source_entry, item.source_entity, item.target_type, item.referenced_target))


def _lookup(name: str, guid: str, names: Mapping[str, list[Mapping[str, Any]]], guids: Mapping[str, list[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    fingerprints: set[tuple[str, ...]] = set()
    guid_matches = guids.get(guid.casefold(), []) if guid else []
    for record in [*names.get(name.casefold(), []), *guid_matches]:
        fingerprint = _record_fingerprint(record)
        if fingerprint not in fingerprints:
            found.append(record)
            fingerprints.add(fingerprint)
    return found


def _kind_matches(candidates: Sequence[Mapping[str, Any]], target_type: str) -> bool:
    if not candidates:
        return False
    wanted = _kind_token(target_type)
    if wanted in {"reference", "script", ""}:
        return True
    if wanted == "worktableworkspace":
        return any(_kind_token(record.get("kind")) == "workspace" for record in candidates)
    return any(
        wanted in _kind_token(record.get("kind"))
        or wanted in _kind_token(record.get("type_id"))
        for record in candidates
    )


def _kind_token(value: Any) -> str:
    """Compare adapter type IDs and canonical kinds independent of separators."""
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _candidate(record: Mapping[str, Any]) -> dict[str, Any]:
    provenance = record.get("provenance") or {}
    return {
        "source_archive": provenance.get("source_archive") or "",
        "source_entry": provenance.get("entry_path") or record.get("entry") or "",
        "kind": record.get("kind") or "object",
        "object_name": record.get("object_name") or "",
    }


def _diagnostic_status(diagnostics: Sequence[Mapping[str, Any]]) -> ReadinessStatus:
    codes = {str(item.get("code") or "") for item in diagnostics}
    if any("ambiguous" in code for code in codes):
        return ReadinessStatus.AMBIGUOUS
    if any("corrupt" in code for code in codes):
        return ReadinessStatus.CORRUPT
    return ReadinessStatus.UNSUPPORTED


def _compatibility_view(result: FullExportReadiness) -> dict[str, Any]:
    payload = result.to_dict()
    if result.status in {ReadinessStatus.COMPLETE, ReadinessStatus.COMPLETE_WITH_WARNINGS}:
        legacy_status = "likely_full_export"
        summary = (
            "The ZEIA has full-system export evidence; stale references in unrelated scripts "
            "were retained as warnings."
            if result.warnings
            else "The ZEIA includes scripts and all required references."
        )
    elif result.status == ReadinessStatus.PARTIAL and result.approved_partial:
        legacy_status = "approved_partial_zeia"
        summary = "The source export is partial, but explicit approval was recorded."
    else:
        legacy_status = "needs_user"
        summary = "The ZEIA is incomplete or has unresolved required dependencies."
    blocking = []
    for item in result.unresolved_references:
        if result.status != ReadinessStatus.COMPLETE_WITH_WARNINGS:
            blocking.append({"id": _legacy_reference_id(item), "summary": "Required dependency could not be resolved.", "items": [item.to_dict()]})
    for item in result.conflicts:
        blocking.append({"id": "ambiguous_identifier", "summary": "Identifier resolves to multiple archive entities.", "items": [item.to_dict()]})
    for diagnostic in result.diagnostics:
        blocking.append(
            diagnostic
            if diagnostic.get("id")
            else {"id": diagnostic.get("code") or "readiness_diagnostic", **diagnostic}
        )
    payload_warnings = list(payload.get("warnings") or [])
    payload.update(
        {
            "required": True,
            "status": legacy_status,
            "readiness_status": result.status.value,
            "accepted": result.accepted,
            "summary": summary,
            "ask_user": (
                "Ask the user for a full FluentControl ZEIA export that includes the source "
                "scripts and their referenced worktables, liquid classes, labware/system "
                "objects, and other dependencies. Wait for that export, or get explicit "
                "permission before continuing with the current partial/non-full export."
            ),
            "blocking_findings": blocking,
            "warnings": payload_warnings,
            "approved_partial_zeia": result.approved_partial,
        }
    )
    return payload


def _legacy_reference_id(item: UnresolvedReference) -> str:
    target = item.target_type.casefold()
    if target == "worktableworkspace":
        return "missing_referenced_worktables"
    if target == "liquid_classes":
        return "missing_liquid_class_objects"
    return "unresolved_script_references"


__all__ = [
    "FullExportReadiness",
    "IdentifierConflict",
    "ReadinessStatus",
    "UnresolvedReference",
    "resolve_full_export_readiness",
    "resolve_manifest_readiness",
]
