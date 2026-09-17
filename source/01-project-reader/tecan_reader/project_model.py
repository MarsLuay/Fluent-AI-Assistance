"""Versioned canonical model for complete FluentControl exports.

The reader owns the boundary between archive-specific files and downstream
project/index/generation code.  Records remain JSON-compatible dictionaries so
existing consumers can migrate without learning an adapter's private types.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


CANONICAL_PROJECT_MODEL_SCHEMA_VERSION = "tecan.canonical_project.v1"


def build_completeness_metadata(
    *,
    script_limit: int | None,
    object_limit: int | None,
    eligible_member_counts: Mapping[str, int],
    summarized_counts: Mapping[str, int],
    errors: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
    oversized_members: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Describe whether an inspection saw every eligible project member."""
    eligible = {
        key: int(value or 0) for key, value in eligible_member_counts.items()
    }
    summarized = {
        key: int(value or 0) for key, value in summarized_counts.items()
    }
    truncated_scripts = (
        max(eligible.get("scripts", 0) - int(script_limit), 0)
        if script_limit is not None
        else 0
    )
    truncated_objects = (
        max(eligible.get("objects", 0) - int(object_limit), 0)
        if object_limit is not None
        else 0
    )
    blocking_errors = [
        error
        for error in errors
        if str(error.get("severity") or "error") != "warning"
    ]
    limited = script_limit is not None or object_limit is not None
    oversized = sorted({str(entry) for entry in oversized_members if entry})
    complete = (
        not limited
        and not truncated_scripts
        and not truncated_objects
        and not blocking_errors
        and not oversized
    )
    return {
        "complete": complete,
        "mode": "complete" if not limited else "preview",
        "configured_limits": {
            "scripts": script_limit,
            "objects": object_limit,
        },
        "eligible_member_counts": dict(sorted(eligible.items())),
        "summarized_counts": dict(sorted(summarized.items())),
        "truncated_scripts": truncated_scripts,
        "truncated_objects": truncated_objects,
        "error_count": len(errors),
        "blocking_error_count": len(blocking_errors),
        "oversized_member_count": len(oversized),
        "oversized_members": oversized,
    }


@dataclass(frozen=True)
class SourceProvenance:
    """Traceability for one normalized archive entity."""

    source_archive: str
    entry_path: str
    original_identifiers: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_archive": self.source_archive,
            "entry_path": self.entry_path,
            "original_identifiers": dict(sorted(self.original_identifiers.items())),
        }


def _entry_path(record: Mapping[str, Any]) -> str:
    value = record.get("entry") or record.get("source") or ""
    return str(value).replace("\\", "/")


def _identifiers(record: Mapping[str, Any]) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for key in (
        "guid",
        "script_guid",
        "component_guid",
        "site_guid",
        "workspace_guid",
        "type_id",
        "object_name",
        "checksum",
    ):
        value = record.get(key)
        if value not in (None, ""):
            identifiers[key] = str(value)
    return identifiers


def normalize_record(
    record: Mapping[str, Any],
    *,
    kind: str,
    source_archive: str | Path,
) -> dict[str, Any]:
    """Normalize a reader record and attach stable provenance.

    The original fields are intentionally retained.  New downstream code can
    use the canonical fields while legacy index/report consumers continue to
    receive the fields they already understand.
    """

    out = deepcopy(dict(record))
    entry = _entry_path(out)
    out["kind"] = kind
    out["source"] = entry
    out["entry"] = entry
    metadata = out.get("source_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    out["source_metadata"] = deepcopy(metadata)
    out["provenance"] = SourceProvenance(
        str(Path(source_archive).resolve()), entry, _identifiers(out)
    ).to_dict()
    return out


@dataclass
class CanonicalProjectModel:
    """JSON-serializable normalized representation of a ZEIA export."""

    source_archive: str
    adapter_id: str
    detection: dict[str, Any]
    scripts: list[dict[str, Any]]
    objects: list[dict[str, Any]]
    worklists: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    source_metadata: dict[str, Any]
    completeness: dict[str, Any] = field(default_factory=dict)

    @property
    def schema_version(self) -> str:
        return CANONICAL_PROJECT_MODEL_SCHEMA_VERSION

    @property
    def entities(self) -> list[dict[str, Any]]:
        """Return compact canonical entities without duplicating full payloads."""
        records: list[dict[str, Any]] = []
        for record in (*self.scripts, *self.objects, *self.worklists):
            records.append(
                {
                    "kind": record.get("kind", ""),
                    "name": record.get("object_name") or record.get("source") or "",
                    "provenance": deepcopy(record.get("provenance") or {}),
                    "source_metadata": deepcopy(record.get("source_metadata") or {}),
                }
            )
        return records

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_archive": self.source_archive,
            "adapter_id": self.adapter_id,
            "detection": deepcopy(self.detection),
            "scripts": deepcopy(self.scripts),
            "objects": deepcopy(self.objects),
            "worklists": deepcopy(self.worklists),
            "entities": self.entities,
            "errors": deepcopy(self.errors),
            "source_metadata": deepcopy(self.source_metadata),
            "completeness": deepcopy(self.completeness),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def inspection_report(self) -> dict[str, Any]:
        """Project-reader compatibility view backed by this canonical model."""
        scripts = deepcopy(self.scripts)
        objects = deepcopy(self.objects)
        worklists = deepcopy(self.worklists)
        family_counts: Counter[str] = Counter()
        command_counts: Counter[str] = Counter()
        warning_counts: Counter[str] = Counter()
        for script in scripts:
            family_counts.update(script.get("family_counts") or {})
            command_counts.update(script.get("command_counts") or {})
            warning_counts.update(script.get("warnings") or [])
        names = [script.get("object_name") or script.get("source") for script in scripts]
        completeness = deepcopy(self.completeness)
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
                errors=self.errors,
            )
        return {
            "kind": "zeia",
            "source": self.source_archive,
            "entry_count": self.source_metadata.get("entry_count", 0),
            "extension_counts": deepcopy(self.source_metadata.get("extension_counts") or {}),
            "script_count_total": int(self.source_metadata.get("script_count_total", len(scripts))),
            "script_count_summarized": len(scripts),
            "object_count_summarized": len(objects),
            "gwl_count_summarized": len(worklists),
            "script_names": names,
            "family_counts": dict(family_counts.most_common()),
            "command_counts": dict(command_counts.most_common(40)),
            "warning_counts": dict(warning_counts.most_common()),
            "scripts": scripts,
            "objects": objects,
            "gwls": worklists,
            "errors": deepcopy(self.errors),
            "complete": bool(completeness.get("complete")),
            "truncated_scripts": int(completeness.get("truncated_scripts") or 0),
            "truncated_objects": int(completeness.get("truncated_objects") or 0),
            "configured_limits": deepcopy(completeness.get("configured_limits") or {}),
            "eligible_member_counts": deepcopy(completeness.get("eligible_member_counts") or {}),
            "summarized_counts": deepcopy(completeness.get("summarized_counts") or {}),
            "completeness": completeness,
        }

    def context_records(
        self, *, root: str | Path
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Map canonical records to the imported-context shape."""
        root_path = Path(root)

        def with_context_path(record: Mapping[str, Any]) -> dict[str, Any]:
            out = deepcopy(dict(record))
            entry = _entry_path(out)
            relative = PurePosixPath("extracted", *PurePosixPath(entry).parts).as_posix()
            out["entry"] = entry
            out["source"] = entry
            out["extracted_path"] = relative
            out["absolute_extracted_path"] = str(root_path / relative)
            return out

        return (
            [with_context_path(record) for record in self.scripts],
            [with_context_path(record) for record in self.objects],
            [with_context_path(record) for record in self.worklists],
        )

    @classmethod
    def from_inspection(
        cls,
        report: Mapping[str, Any],
        *,
        source_archive: str | Path,
        adapter_id: str = "legacy-inspection",
        detection: Mapping[str, Any] | None = None,
    ) -> "CanonicalProjectModel":
        """Lift a compatibility report into the canonical model.

        This fallback keeps old callers and test doubles safe while real archive
        ingestion always constructs the model directly through an adapter.
        """
        scripts = [
            normalize_record(item, kind="script", source_archive=source_archive)
            for item in report.get("scripts", [])
            if isinstance(item, Mapping)
        ]
        objects = [
            normalize_record(item, kind=str(item.get("kind") or "object"), source_archive=source_archive)
            for item in report.get("objects", [])
            if isinstance(item, Mapping)
        ]
        worklists = [
            normalize_record(item, kind="worklist", source_archive=source_archive)
            for item in report.get("gwls", [])
            if isinstance(item, Mapping)
        ]
        metadata = {
            "entry_count": report.get("entry_count", 0),
            "extension_counts": deepcopy(report.get("extension_counts") or {}),
            "script_count_total": report.get("script_count_total", len(scripts)),
        }
        completeness = deepcopy(report.get("completeness") or {})
        if not completeness:
            completeness = build_completeness_metadata(
                script_limit=(report.get("configured_limits") or {}).get("scripts"),
                object_limit=(report.get("configured_limits") or {}).get("objects"),
                eligible_member_counts=report.get("eligible_member_counts") or {
                    "scripts": report.get("script_count_total", len(scripts)),
                    "objects": len(objects),
                    "worklists": len(worklists),
                },
                summarized_counts=report.get("summarized_counts") or {
                    "scripts": len(scripts),
                    "objects": len(objects),
                    "worklists": len(worklists),
                },
                errors=report.get("errors") or [],
                oversized_members=report.get("oversized_members") or (),
            )
        metadata["completeness"] = deepcopy(completeness)
        return cls(
            source_archive=str(Path(source_archive).resolve()),
            adapter_id=adapter_id,
            detection=deepcopy(dict(detection or {})),
            scripts=scripts,
            objects=objects,
            worklists=worklists,
            errors=deepcopy(list(report.get("errors") or [])),
            source_metadata=metadata,
            completeness=completeness,
        )


class InspectionReport(dict[str, Any]):
    """Dict-compatible public report carrying its canonical backing model."""

    def __init__(self, model: CanonicalProjectModel):
        super().__init__(model.inspection_report())
        self.canonical_model = model
