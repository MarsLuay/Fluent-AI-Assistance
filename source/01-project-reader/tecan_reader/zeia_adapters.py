"""Structural ZEIA probes and adapters for the canonical project model."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import re
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
import zipfile

from tecan_common.xml_compat import MAX_XML_BYTES
from tecan_common.zeia_limits import (
    MAX_ZEIA_COMPRESSION_RATIO,
    MAX_ZEIA_ENTRY_COUNT,
    MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES,
    MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    ZeiaArchiveInventory,
    ZeiaArchiveValidationError,
    build_zeia_archive_inventory,
    normalize_zeia_member_name,
)

from .common import extension_counts
from .diagnostics import (
    DiagnosticCode,
    IngestionArchiveError,
    make_diagnostic,
    sort_diagnostics,
)
from .gwl import inspect_gwl_lines
from .project_model import (
    CanonicalProjectModel,
    InspectionReport,
    build_completeness_metadata,
    derive_base_worktable_identity,
    normalize_record,
)
from .script import inspect_xscr_text
from .scheduler import inspect_scheduler_text, is_scheduler_artifact
from .task_input import inspect_twl_text
from .xmlobj import inspect_xml_object_text


@dataclass(frozen=True)
class ArchiveProbe:
    """Bounded structural evidence used by adapters."""

    entries: tuple[str, ...]
    xml_samples: tuple[tuple[str, str], ...]
    versions: tuple[str, ...]
    has_visionx_namespace: bool
    has_script_structure: bool
    has_script_container_evidence: bool
    has_worklist_structure: bool
    has_task_input_structure: bool
    has_object_structure: bool


@dataclass(frozen=True)
class AdapterMatch:
    adapter_id: str
    format_family: str
    confidence: float
    strong: bool
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "format_family": self.format_family,
            "confidence": self.confidence,
            "strong": self.strong,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class DetectionResult:
    status: str
    entries: tuple[str, ...]
    matches: tuple[AdapterMatch, ...]
    diagnostics: tuple[str, ...] = ()
    diagnostic_records: tuple[dict[str, Any], ...] = ()
    software_family: dict[str, Any] | None = None

    @property
    def selected(self) -> AdapterMatch | None:
        if self.status != "supported" or not self.matches:
            return None
        return sorted(self.matches, key=lambda item: (-item.confidence, item.adapter_id))[0]

    def to_dict(self) -> dict[str, Any]:
        selected = self.selected
        family = self.software_family or unknown_software_family()
        return {
            "status": self.status,
            "entries": list(self.entries),
            "matches": [match.to_dict() for match in self.matches],
            "selected_adapter": selected.adapter_id if selected else None,
            "format_family": selected.format_family if selected else None,
            "software_family": family,
            "diagnostics": list(self.diagnostics),
            "diagnostic_records": sort_diagnostics(self.diagnostic_records),
        }


class ZeiaAdapter(Protocol):
    """Contract implemented by a structural ZEIA/export-family adapter."""

    adapter_id: str
    format_family: str

    def probe(self, archive: ArchiveProbe) -> AdapterMatch | None:
        """Return evidence when the adapter recognizes the archive structure."""


class ZeiaFormatError(ValueError):
    """Raised when a ZEIA is unsupported or structurally ambiguous."""

    def __init__(self, result: DetectionResult):
        self.result = result
        detail = "; ".join(result.diagnostics) or "no matching adapter"
        super().__init__(f"ZEIA format is {result.status}: {detail}")


class _VisionXVersionAdapter:
    def __init__(self, version: str):
        self.version = version
        self.adapter_id = f"visionx-datastore-v{version}"
        self.format_family = "visionx-datastore"

    def probe(self, archive: ArchiveProbe) -> AdapterMatch | None:
        if self.version not in archive.versions:
            return None
        return AdapterMatch(
            self.adapter_id,
            self.format_family,
            1.0,
            True,
            (
                f"dataStoreVersion={self.version}",
                "archive contains structural XML export evidence",
            ),
        )


class _VisionXNamespaceAdapter:
    adapter_id = "visionx-datastore"
    format_family = "visionx-datastore"

    def probe(self, archive: ArchiveProbe) -> AdapterMatch | None:
        if not archive.has_visionx_namespace or archive.versions:
            return None
        return AdapterMatch(
            self.adapter_id,
            self.format_family,
            0.95,
            True,
            ("VisionX VxData namespace", "archive contains structural XML export evidence"),
        )


class _GenericStructuredAdapter:
    adapter_id = "structured-zeia"
    format_family = "structured-zeia"

    def probe(self, archive: ArchiveProbe) -> AdapterMatch | None:
        if archive.versions:
            return None
        if not (
            archive.has_script_structure
            or archive.has_object_structure
            or archive.has_script_container_evidence
            or archive.has_worklist_structure
            or archive.has_task_input_structure
        ):
            return None
        evidence = []
        if archive.has_script_structure:
            evidence.append("script XML contains ObjectName/Script structure")
        if archive.has_object_structure:
            evidence.append("catalog XML contains ObjectName or typed object structure")
        if archive.has_script_container_evidence and not archive.has_script_structure:
            evidence.append("recognized script container contains XML-like content")
        if archive.has_worklist_structure:
            evidence.append("worklist entry contains recognized transfer records")
        if archive.has_task_input_structure:
            evidence.append("TWL entry contains Task Input records")
        return AdapterMatch(self.adapter_id, self.format_family, 0.7, False, tuple(evidence))


XML_OBJECT_EXTS = {".xcmp", ".xwsp", ".xlqc", ".xlcp", ".xsit", ".xcon", ".xml"}
ASSET_EXTS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}
TASK_INPUT_EXTS = {".twl"}


ADAPTERS: tuple[ZeiaAdapter, ...] = (
    _VisionXVersionAdapter("2"),
    _VisionXVersionAdapter("3"),
    _VisionXNamespaceAdapter(),
    _GenericStructuredAdapter(),
)


def probe_zeia(
    path: str | Path,
    *,
    max_entry_count: int = MAX_ZEIA_ENTRY_COUNT,
    max_total_uncompressed_bytes: int = MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    max_member_uncompressed_bytes: int | None = MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = MAX_ZEIA_COMPRESSION_RATIO,
) -> DetectionResult:
    """Probe archive structure without guessing from the filename/extension."""

    archive_path = Path(path).expanduser().resolve()
    if not archive_path.exists():
        message = f"archive not found: {archive_path}"
        return DetectionResult(
            "unsupported", (), (), (message,),
            (make_diagnostic(
                DiagnosticCode.INPUT_NOT_FOUND, message,
                archive_path=str(archive_path),
                next_action="Provide an existing .zeia archive or export directory.",
            ),),
        )
    if not zipfile.is_zipfile(archive_path):
        message = f"not a readable ZIP archive: {archive_path}"
        return DetectionResult(
            "unsupported", (), (), (message,),
            (make_diagnostic(
                DiagnosticCode.ARCHIVE_INVALID, message,
                archive_path=str(archive_path),
                next_action="Provide an uncorrupted FluentControl .zeia ZIP export.",
            ),),
        )
    try:
        with zipfile.ZipFile(archive_path) as zf:
            inventory = build_zeia_archive_inventory(
                zf,
                max_entry_count=max_entry_count,
                max_total_uncompressed_bytes=max_total_uncompressed_bytes,
                max_member_uncompressed_bytes=max_member_uncompressed_bytes,
                max_compression_ratio=max_compression_ratio,
            )
            archive = _build_probe(zf, inventory)
    except ZeiaArchiveValidationError as exc:
        raise IngestionArchiveError(
            str(exc), _archive_validation_diagnostic(archive_path, exc)
        ) from exc
    except zipfile.BadZipFile as exc:
        # Preserve the legacy exception contract while making safety failures
        # machine-readable for readiness and CLI callers.
        text = str(exc)
        code = DiagnosticCode.ARCHIVE_INVALID
        diagnostic = make_diagnostic(
            code,
            text or "ZEIA archive exceeded a configured safety limit.",
            archive_path=str(archive_path),
            next_action="Reduce the export or raise the configured reader limit after review.",
            exception=exc,
        )
        raise IngestionArchiveError(text, diagnostic) from exc
    except (OSError, ValueError) as exc:
        message = f"archive safety/format validation failed: {exc}"
        return DetectionResult(
            "unsupported",
            (),
            (),
            (message,),
            (make_diagnostic(
                DiagnosticCode.ARCHIVE_INVALID,
                message,
                archive_path=str(archive_path),
                next_action="Provide a readable ZIP archive with safe member paths.",
                exception=exc,
            ),),
        )

    return _detection_from_probe(archive, archive_path)


def unknown_software_family() -> dict[str, Any]:
    """Older models omitted family evidence. Absence stays unknown, not FluentControl."""
    return {
        "software_family": "unknown",
        "status": "unknown",
        "evidence": [],
        "compatibility": "missing-family-evidence-is-unknown",
    }


def classify_software_family(archive: ArchiveProbe) -> dict[str, Any]:
    """Classify product family only from explicit names in sampled archive text.

    The ``.zeia`` suffix and structural format never select a family.
    """
    markers = (
        ("fluentcontrol", re.compile(r"FluentControl")),
        ("vcontrol", re.compile(r"\bvControl\b", re.IGNORECASE)),
        ("veya", re.compile(r"\bVeya\b")),
    )
    evidence: list[dict[str, str]] = []
    families: list[str] = []
    for member, text in archive.xml_samples:
        for family, pattern in markers:
            if family in families:
                continue
            if pattern.search(text):
                families.append(family)
                evidence.append({
                    "family": family,
                    "member": member,
                    "marker": pattern.pattern,
                })
    if len(families) > 1:
        family, status = "conflicting", "conflicting"
    elif len(families) == 1:
        family, status = families[0], "verified"
    else:
        family, status = "unknown", "unknown"
    return {
        "software_family": family,
        "status": status,
        "evidence": evidence,
        "compatibility": "explicit-product-name-only",
    }


def _detection_from_probe(archive: ArchiveProbe, archive_path: Path) -> DetectionResult:
    """Resolve adapter matches from one already validated probe."""
    matches = tuple(match for adapter in ADAPTERS if (match := adapter.probe(archive)) is not None)
    strong = tuple(match for match in matches if match.strong)
    if len(strong) > 1:
        diagnostics = tuple(
            f"{match.adapter_id} matched: {', '.join(match.evidence)}"
            for match in strong
        )
        records = tuple(
            make_diagnostic(
                DiagnosticCode.ZEIA_AMBIGUOUS_FORMAT,
                message,
                archive_path=str(archive_path),
                adapter_id=match.adapter_id,
                next_action="Provide one supported export variant or remove conflicting structural evidence.",
            )
            for match, message in zip(strong, diagnostics)
        )
        return DetectionResult(
            "ambiguous", archive.entries, matches, diagnostics, records,
            software_family=classify_software_family(archive),
        )
    if not matches:
        diagnostics = _unsupported_diagnostics(archive)
        return DetectionResult(
            "unsupported", archive.entries, (), diagnostics,
            tuple(make_diagnostic(
                DiagnosticCode.ZEIA_UNKNOWN_FORMAT,
                message,
                archive_path=str(archive_path),
                next_action="Provide a supported FluentControl ZEIA export variant.",
            ) for message in diagnostics),
            software_family=classify_software_family(archive),
        )
    return DetectionResult(
        "supported", archive.entries, matches,
        software_family=classify_software_family(archive),
    )


def detect_zeia_format(path: str | Path, **kwargs: Any) -> DetectionResult:
    """Descriptive alias for callers that want the format-probe terminology."""
    return probe_zeia(path, **kwargs)


def resolve_adapter(path: str | Path, **kwargs: Any) -> DetectionResult:
    """Resolve a format while preserving unsupported/ambiguous outcomes."""
    return probe_zeia(path, **kwargs)


def ingest_zeia(
    path: str | Path,
    *,
    script_limit: int | None = None,
    object_limit: int | None = None,
    max_entry_count: int = MAX_ZEIA_ENTRY_COUNT,
    max_total_uncompressed_bytes: int = MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    max_member_uncompressed_bytes: int | None = MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = MAX_ZEIA_COMPRESSION_RATIO,
    _archive: zipfile.ZipFile | None = None,
    _inventory: ZeiaArchiveInventory | None = None,
) -> CanonicalProjectModel:
    """Normalize one complete export through the selected adapter."""

    archive_path = Path(path).expanduser().resolve()
    if _archive is None and (not archive_path.exists() or not zipfile.is_zipfile(archive_path)):
        detection = probe_zeia(
            archive_path,
            max_entry_count=max_entry_count,
            max_total_uncompressed_bytes=max_total_uncompressed_bytes,
            max_member_uncompressed_bytes=max_member_uncompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        )
        raise ZeiaFormatError(detection)
    errors: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    worklists: list[dict[str, Any]] = []
    scheduler_processes: list[dict[str, Any]] = []
    scheduler_tasks: list[dict[str, Any]] = []
    task_inputs: list[dict[str, Any]] = []
    eligible_member_counts = {"scripts": 0, "objects": 0, "worklists": 0, "task_inputs": 0}
    oversized_members: list[str] = []
    try:
        archive_context = (
            nullcontext(_archive)
            if _archive is not None
            else zipfile.ZipFile(archive_path)
        )
        with archive_context as zf:
            inventory = _inventory or build_zeia_archive_inventory(
                zf,
                max_entry_count=max_entry_count,
                max_total_uncompressed_bytes=max_total_uncompressed_bytes,
                max_member_uncompressed_bytes=max_member_uncompressed_bytes,
                max_compression_ratio=max_compression_ratio,
            )
            detection = _detection_from_probe(
                _build_probe(zf, inventory), archive_path
            )
            if detection.status != "supported":
                raise ZeiaFormatError(detection)
            selected = detection.selected
            if selected is None:  # pragma: no cover - guarded by DetectionResult
                raise ZeiaFormatError(
                    DetectionResult("unsupported", detection.entries, (), ("no adapter selected",))
                )
            eligible_member_counts = _eligible_member_counts(
                inventory.names
            )
            oversized_members = [
                member.name
                for member in inventory.members
                for info in (member.info,)
                if not info.is_dir()
                and info.file_size > MAX_XML_BYTES
                and Path(member.name).suffix.casefold() in {".xscr", *XML_OBJECT_EXTS}
                and not _is_known_irrelevant_metadata(member.name)
            ]
            for member in inventory.members:
                info = member.info
                if info.is_dir():
                    continue
                entry = member.name
                suffix = Path(entry).suffix.casefold()
                if suffix == ".xscr":
                    if script_limit is not None and len(scripts) >= script_limit:
                        continue
                    try:
                        scripts.append(
                            normalize_record(
                                _inspect_script_member(
                                    _read_member_data(zf, info),
                                    entry,
                                    size_bytes=info.file_size,
                                ),
                                kind="script",
                                source_archive=archive_path,
                            )
                        )
                    except (OSError, zipfile.BadZipFile):
                        raise
                    except Exception as exc:
                        errors.append(
                            _parse_error(entry, exc, suffix=suffix, parser="xscr", archive_path=archive_path)
                        )
                elif suffix == ".twl":
                    try:
                        data = _read_member_data(zf, info)
                        task_inputs.append(
                            normalize_record(
                                inspect_twl_text(data.decode("utf-8-sig"), source_name=entry),
                                kind="task_input",
                                source_archive=archive_path,
                            )
                        )
                    except (OSError, zipfile.BadZipFile):
                        raise
                    except Exception as exc:
                        errors.append(
                            _parse_error(entry, exc, suffix=suffix, parser="twl", archive_path=archive_path)
                        )
                elif suffix == ".gwl":
                    try:
                        data = _read_member_data(zf, info)
                        if info.file_size > MAX_XML_BYTES:
                            oversized_members.append(entry)
                        text = data.decode("utf-8-sig")
                        worklists.append(
                            normalize_record(
                                inspect_gwl_lines(text.splitlines(), source_name=entry),
                                kind="worklist",
                                source_archive=archive_path,
                            )
                        )
                    except (OSError, zipfile.BadZipFile):
                        raise
                    except Exception as exc:
                        errors.append(
                            _parse_error(entry, exc, suffix=suffix, parser="gwl", archive_path=archive_path)
                        )
                elif suffix in XML_OBJECT_EXTS:
                    if object_limit is not None and len(objects) >= object_limit:
                        continue
                    try:
                        raw_data = _read_member_data(zf, info)
                        raw_text = raw_data.decode("utf-8-sig", errors="replace")
                        if suffix == ".xml" and is_scheduler_artifact(entry, raw_text):
                            scheduler = inspect_scheduler_text(raw_text, source_name=entry)
                            for process in scheduler.get("scheduler_processes", []):
                                scheduler_processes.append(
                                    normalize_record(process, kind="scheduler_process", source_archive=archive_path)
                                )
                            for task in scheduler.get("scheduler_tasks", []):
                                scheduler_tasks.append(
                                    normalize_record(task, kind="scheduler_task", source_archive=archive_path)
                                )
                            if scheduler_processes or scheduler_tasks:
                                continue
                        record = normalize_record(
                            _inspect_object_member(
                                raw_data,
                                entry,
                                suffix,
                                size_bytes=info.file_size,
                            ),
                            kind=_object_kind(suffix),
                            source_archive=archive_path,
                        )
                        objects.append(record)
                        diagnostic = _object_subtype_diagnostic(record, entry, suffix)
                        if diagnostic is not None:
                            errors.append(diagnostic)
                    except (OSError, zipfile.BadZipFile):
                        raise
                    except Exception as exc:
                        # Preserve every failed member as a classified diagnostic;
                        # completeness and readiness decide whether it is blocking.
                        errors.append(
                            _parse_error(
                                entry,
                                exc,
                                suffix=suffix,
                                parser=f"xml:{suffix.lstrip('.')}",
                                archive_path=archive_path,
                            )
                        )
                elif suffix in ASSET_EXTS:
                    if object_limit is not None and len(objects) >= object_limit:
                        continue
                    objects.append(
                        normalize_record(
                            {
                                "kind": "asset",
                                "source": entry,
                                "entry": entry,
                                "object_name": Path(entry).name,
                                "type_id": suffix.lstrip("."),
                                "functional_group": "asset",
                                "footprint": "",
                                "renderer": "",
                                "names": [Path(entry).name],
                                "guids": [],
                                "pin_refs": [],
                                "asset_refs": [Path(entry).name],
                                "custom_part": True,
                                "source_metadata": {"asset_extension": suffix},
                            },
                            kind="asset",
                            source_archive=archive_path,
                        )
                    )
    except ZeiaArchiveValidationError as exc:
        raise IngestionArchiveError(
            str(exc), _archive_validation_diagnostic(archive_path, exc)
        ) from exc
    except (OSError, zipfile.BadZipFile) as exc:
        diagnostic = make_diagnostic(
            DiagnosticCode.ARCHIVE_INVALID,
            str(exc) or "ZEIA archive could not be read.",
            archive_path=str(archive_path),
            next_action="Provide a readable ZIP archive with intact members.",
            exception=exc,
        )
        raise IngestionArchiveError(str(exc), diagnostic) from exc

    summarized_counts = {
        "scripts": len(scripts),
        "objects": len(objects),
        "worklists": len(worklists),
        "task_inputs": len(task_inputs),
    }
    completeness = build_completeness_metadata(
        script_limit=script_limit,
        object_limit=object_limit,
        eligible_member_counts=eligible_member_counts,
        summarized_counts=summarized_counts,
        errors=errors,
        oversized_members=oversized_members,
    )
    source_metadata = {
        "entry_count": len(detection.entries),
        "extension_counts": extension_counts(detection.entries),
        "script_count_total": eligible_member_counts["scripts"],
        "format_family": selected.format_family,
        "software_family": detection.to_dict()["software_family"],
        "adapter_matches": [match.to_dict() for match in detection.matches],
        "completeness": completeness,
        "complete": completeness["complete"],
        "eligible_member_counts": completeness["eligible_member_counts"],
        "summarized_counts": completeness["summarized_counts"],
        "scheduler_process_count": len(scheduler_processes),
        "scheduler_task_count": len(scheduler_tasks),
        "task_input_count": len(task_inputs),
        "truncated_scripts": completeness["truncated_scripts"],
        "truncated_objects": completeness["truncated_objects"],
    }
    return CanonicalProjectModel(
        source_archive=str(archive_path),
        adapter_id=selected.adapter_id,
        detection=detection.to_dict(),
        scripts=scripts,
        objects=objects,
        worklists=worklists,
        errors=errors,
        source_metadata=source_metadata,
        completeness=completeness,
        scheduler_processes=scheduler_processes,
        scheduler_tasks=scheduler_tasks,
        task_inputs=task_inputs,
        base_worktable_identity=derive_base_worktable_identity([*scripts, *objects]),
    )


def ingest_zeia_from_open_archive(
    path: str | Path,
    archive: zipfile.ZipFile,
    *,
    inventory: ZeiaArchiveInventory | None = None,
    script_limit: int | None = None,
    object_limit: int | None = None,
    max_entry_count: int = MAX_ZEIA_ENTRY_COUNT,
    max_total_uncompressed_bytes: int = MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    max_member_uncompressed_bytes: int | None = MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = MAX_ZEIA_COMPRESSION_RATIO,
) -> CanonicalProjectModel:
    """Ingest one already-open archive without rebuilding its inventory.

    Project-context import uses this seam to share one validated inventory with
    extraction. The caller owns the open archive and must not close it until the
    returned model and any associated materialization work are complete.
    """
    return ingest_zeia(
        path,
        script_limit=script_limit,
        object_limit=object_limit,
        max_entry_count=max_entry_count,
        max_total_uncompressed_bytes=max_total_uncompressed_bytes,
        max_member_uncompressed_bytes=max_member_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
        _archive=archive,
        _inventory=inventory,
    )


def inspect_canonical_archive(path: str | Path, **kwargs: Any) -> InspectionReport:
    """Return the legacy report view while retaining its canonical model."""
    return InspectionReport(ingest_zeia(path, **kwargs))


def _build_probe(
    zf: zipfile.ZipFile, inventory: ZeiaArchiveInventory
) -> ArchiveProbe:
    entries = inventory.names
    samples: list[tuple[str, str]] = []
    for member in inventory.members:
        info = member.info
        if info.is_dir() or Path(info.filename).suffix.casefold() not in {
            ".xscr", ".xcmp", ".xwsp", ".xlqc", ".xlcp", ".xsit", ".xcon", ".xml", ".twl"
        }:
            continue
        if len(samples) >= 32:
            break
        try:
            data = _read_prefix(zf, info, 128 * 1024)
            samples.append(
                (member.name, data.decode("utf-8-sig", errors="replace"))
            )
        except (OSError, RuntimeError):
            continue
    combined = "\n".join(text for _name, text in samples)
    versions = tuple(sorted(set(re.findall(r"dataStoreVersion\s*=\s*[\"']([^\"']+)", combined, re.IGNORECASE))))
    has_visionx_namespace = bool(re.search(r"tecan\.com/TSCC/VisionX|<[^>]*:?VxData(?:\s|>)", combined, re.IGNORECASE))
    has_script_structure = bool(
        re.search(r"<[^>]*:?ObjectName\b[^>]*>[^<]+</[^>]*:?ObjectName>", combined, re.IGNORECASE)
        and re.search(r"<[^>]*:?Script(?:\s|>)", combined, re.IGNORECASE)
    )
    has_object_structure = bool(
        re.search(r"<[^>]*:?ObjectName\b[^>]*>", combined, re.IGNORECASE)
        or re.search(r"<[^>]*:?Object\s+[^>]*Type=", combined, re.IGNORECASE)
    )
    has_script_container_evidence = any(
        Path(name).suffix.casefold() == ".xscr"
        and any(
            part in {"scripts", "datastore", "userspecific"}
            for part in PurePosixPath(name.casefold()).parts
        )
        and text.lstrip().startswith("<")
        for name, text in samples
    )
    has_worklist_structure = False
    has_task_input_structure = False
    worklist_samples = 0
    for member in inventory.members:
        info = member.info
        if info.is_dir() or Path(info.filename).suffix.casefold() not in {".gwl", ".twl"}:
            continue
        if worklist_samples >= 32:
            break
        worklist_samples += 1
        if Path(info.filename).suffix.casefold() == ".twl":
            has_task_input_structure = True
            continue
        try:
            text = _read_prefix(zf, info, 128 * 1024).decode(
                "utf-8-sig", errors="replace"
            )
        except (OSError, RuntimeError, zipfile.BadZipFile):
            continue
        if re.search(r"^\s*[ADWB](?:[1-4])?;", text, re.IGNORECASE | re.MULTILINE):
            has_worklist_structure = True
            break
    return ArchiveProbe(
        entries=entries,
        xml_samples=tuple(samples),
        versions=versions,
        has_visionx_namespace=has_visionx_namespace,
        has_script_structure=has_script_structure,
        has_script_container_evidence=has_script_container_evidence,
        has_worklist_structure=has_worklist_structure,
        has_task_input_structure=has_task_input_structure,
        has_object_structure=has_object_structure,
    )


def _unsupported_diagnostics(archive: ArchiveProbe) -> tuple[str, ...]:
    if archive.versions:
        return (f"unsupported dataStoreVersion value(s): {', '.join(archive.versions)}",)
    if not archive.entries:
        return ("archive has no file entries",)
    return ("no supported ZEIA structural evidence (expected script or catalog XML)",)


def _normal_entry(value: str) -> str:
    return normalize_zeia_member_name(value)


def _read_prefix(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    with zf.open(info) as stream:
        return stream.read(limit)


def _read_member_data(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size <= MAX_XML_BYTES:
        return zf.read(info)
    return _read_prefix(zf, info, MAX_XML_BYTES + 1)


def _archive_validation_diagnostic(
    archive_path: Path, exc: ZeiaArchiveValidationError
) -> dict[str, Any]:
    reason = exc.reason
    if reason == "entry_count":
        code = DiagnosticCode.LIMIT_ENTRY_COUNT
        violated_limit = "max_entry_count"
    elif reason == "total_uncompressed_bytes":
        code = DiagnosticCode.LIMIT_UNCOMPRESSED_BYTES
        violated_limit = "max_total_uncompressed_bytes"
    elif reason == "member_uncompressed_bytes":
        code = DiagnosticCode.LIMIT_MEMBER_BYTES
        violated_limit = "max_member_uncompressed_bytes"
    elif reason == "compression_ratio":
        code = DiagnosticCode.LIMIT_COMPRESSION_RATIO
        violated_limit = "max_compression_ratio"
    elif reason == "duplicate_member":
        code = DiagnosticCode.ARCHIVE_DUPLICATE_MEMBER
        violated_limit = "unique_normalized_member_names"
    else:
        code = DiagnosticCode.ARCHIVE_UNSAFE_PATH
        violated_limit = "safe_member_path"
    return make_diagnostic(
        code,
        str(exc),
        archive_path=str(archive_path),
        entry_path=exc.normalized_name or exc.entry_name,
        source_entity=exc.entry_name,
        limit=exc.limit,
        value=(
            exc.uncompressed_size
            if reason not in {"entry_count", "compression_ratio"}
            else (
                exc.uncompressed_size
                if reason == "entry_count"
                else (
                    exc.uncompressed_size / max(exc.compressed_size or 1, 1)
                    if exc.uncompressed_size is not None
                    else None
                )
            )
        ),
        compressed_size=exc.compressed_size,
        uncompressed_size=exc.uncompressed_size,
        violated_limit=violated_limit,
        next_action=(
            "Remove the duplicate or unsafe member path and re-export the archive."
            if reason in {"duplicate_member", "unsafe_path"}
            else "Reduce the export or raise the configured reader limit after review."
        ),
        exception=exc,
    )


def _inspect_script_member(
    data: bytes,
    entry: str,
    *,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    declared_size = int(size_bytes if size_bytes is not None else len(data))
    if declared_size <= MAX_XML_BYTES:
        return inspect_xscr_text(data.decode("utf-8-sig"), source_name=entry)
    text = data[: min(len(data), 1024 * 1024)].decode("utf-8-sig", errors="replace")
    object_name = _xml_value(text, "ObjectName") or Path(entry).stem
    checksum = _xml_value(text, "Checksum")
    commands = re.findall(r"<Object\b[^>]*\bType=['\"]([^'\"]+)", text, re.IGNORECASE)
    return {
        "kind": "xscr",
        "source": entry,
        "entry": entry,
        "object_name": object_name,
        "script_version": "",
        "checksum": checksum,
        "base_worktable_name": _xml_value(text, "BaseWorktableName"),
        "base_worktable_guid": _xml_value(text, "BaseWorktableGuid"),
        "references": [],
        "variables": [],
        "startup_variables": [],
        "query_prompts": [],
        "operator_prompts": [],
        "set_variables": [],
        "command_count": len(commands),
        "command_counts": {},
        "family_counts": {},
        "commands": [],
        "dependencies": {},
        "comments": [],
        "warnings": [],
        "source_metadata": {
            "oversized_xml": True,
            "size_bytes": declared_size,
            "unknown_fields": _unknown_fields_from_text(text),
        },
    }


def _inspect_object_member(
    data: bytes,
    entry: str,
    suffix: str,
    *,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    declared_size = int(size_bytes if size_bytes is not None else len(data))
    if declared_size <= MAX_XML_BYTES:
        return inspect_xml_object_text(data.decode("utf-8-sig"), source_name=entry, suffix=suffix)
    text = data[: min(len(data), 1024 * 1024)].decode("utf-8-sig", errors="replace")
    return {
        "kind": _object_kind(suffix),
        "source": entry,
        "entry": entry,
        "object_name": Path(entry).stem,
        "type_id": _xml_value(text, "TypeId"),
        "functional_group": _xml_value(text, "FunctionalGroup"),
        "footprint": _xml_value(text, "FootPrint"),
        "renderer": _xml_value(text, "Renderer"),
        "description": "",
        "component_guid": _xml_value(text, "ComponentGuid"),
        "site_guid": _xml_value(text, "SiteGuid"),
        "base_worktable_name": _xml_value(text, "BaseWorktableName"),
        "base_worktable_guid": _xml_value(text, "BaseWorktableGuid"),
        "names": [],
        "guids": [],
        "pin_refs": [],
        "asset_refs": [],
        "custom_part": False,
        "oversized_xml": True,
        "size_bytes": declared_size,
        "source_metadata": {
            "oversized_xml": True,
            "size_bytes": declared_size,
            "unknown_fields": _unknown_fields_from_text(text),
        },
    }


def _xml_value(text: str, name: str) -> str:
    match = re.search(rf"<(?:[A-Za-z_][\w.-]*:)?{re.escape(name)}(?:\s[^>]*)?>(.*?)</(?:[A-Za-z_][\w.-]*:)?{re.escape(name)}>", text, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _unknown_fields_from_text(text: str) -> dict[str, list[str]]:
    known = {
        "VxData", "Payload", "PayloadData", "Script", "Properties", "Commands",
        "Object", "Reference", "Guid", "GUID", "ObjectName", "ObjectSubfolderPath",
        "Checksum", "TypeName", "Scope", "QueryOnStartup", "QueryOnStartupString",
        "ReadOnly", "Values", "VariableDefinitionHelper", "QueryVariableStatement",
        "SetVariableStatement", "VariableDeclarations", "ScriptGroup", "Objects",
        "RUPVariableStatement", "RUPWorktableStatement", "RUPStandardStatement",
        "RupVariableItem", "VariableDatas", "VariableDataModel", "Variables",
        "Instructions", "RUPDisplayAndWait", "RUPAutoClose", "RUPTimeOut",
        "VariableName", "DisplayText", "DisplayType", "AllowedValues", "IsEnabled",
        "Name", "Comment", "LineNumber", "Condition", "LoopVariable", "NumberOfLoops",
        "Value", "QueryPrompt", "MinimumText", "MaximumText", "LabwareName",
        "LabwareLable", "LabwareLabel", "LabwareType", "RackLabel", "RackType",
        "Location", "Position", "LiquidClassName", "LiquidClassNameBySelection",
        "Volume", "DeviceAlias", "AvailableID", "ScriptName", "MethodName",
        "ApplicationName", "FileName", "Path", "WorklistName", "SubRoutine",
        "Barcode", "CustomDetailImageFilePath", "PinNumber", "RUPScreenTitle",
        "TypeId", "FunctionalGroup", "FootPrint", "Renderer", "Description",
        "BaseWorktableName", "BaseWorktableGuid", "ComponentGuid", "SiteGuid",
    }
    values: dict[str, list[str]] = {}
    pattern = re.compile(
        r"<(?:(?:[A-Za-z_][\w.-]*):)?([A-Za-z_][\w.-]*)(?:\s[^>]*)?>(.*?)</\1>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        name = match.group(1)
        value = re.sub(r"\s+", " ", match.group(2)).strip()
        if not value or "<" in value or name in known:
            continue
        bucket = values.setdefault(name, [])
        if value not in bucket and len(bucket) < 20:
            bucket.append(value)
    return {name: values[name] for name in sorted(values)}


def _object_kind(suffix: str) -> str:
    return {
        ".xcmp": "component",
        ".xwsp": "workspace",
        ".xlqc": "liquid_class",
        ".xlcp": "liquid_class_map",
        ".xsit": "site",
        ".xcon": "connector",
        ".xml": "xml",
    }.get(suffix, suffix.lstrip(".") or "xml")


def _eligible_member_counts(entries: list[str] | tuple[str, ...]) -> dict[str, int]:
    return {
        "scripts": sum(1 for entry in entries if Path(entry).suffix.casefold() == ".xscr"),
        "objects": sum(
            1
            for entry in entries
            if Path(entry).suffix.casefold() in XML_OBJECT_EXTS | ASSET_EXTS
        ),
        "worklists": sum(1 for entry in entries if Path(entry).suffix.casefold() == ".gwl"),
        "task_inputs": sum(1 for entry in entries if Path(entry).suffix.casefold() in TASK_INPUT_EXTS),
    }


def _parse_error(
    entry: str,
    exc: Exception,
    *,
    suffix: str,
    parser: str,
    archive_path: str | Path | None = None,
) -> dict[str, Any]:
    if _is_known_irrelevant_metadata(entry):
        classification = "known_irrelevant_metadata"
        severity = "warning"
        code = DiagnosticCode.PARSER_FAILED
    elif isinstance(exc, (OSError, EOFError, RuntimeError, zipfile.BadZipFile)):
        classification = "unreadable_or_encoding_failure"
        severity = "error"
        code = DiagnosticCode.ARCHIVE_INVALID if parser == "archive" else DiagnosticCode.INPUT_UNREADABLE
    elif isinstance(exc, UnicodeDecodeError):
        classification = "unreadable_or_encoding_failure"
        severity = "error"
        code = DiagnosticCode.ENCODING_DECODE_FAILED
    elif type(exc).__name__.lower().endswith("parseerror"):
        classification = "malformed"
        severity = "error"
        code = DiagnosticCode.ZEIA_SCHEMA_MALFORMED
    else:
        classification = "parser_failure"
        severity = "error"
        code = DiagnosticCode.PARSER_FAILED
    message = (
        "known metadata could not be parsed and was retained as a warning"
        if classification == "known_irrelevant_metadata"
        else f"{type(exc).__name__}: {exc}"
    )
    diagnostic = make_diagnostic(
        code,
        message,
        severity=severity,
        archive_path=str(Path(archive_path).resolve()) if archive_path else None,
        entry_path=entry,
        source_entity=entry,
        next_action=(
            "Re-export the archive or repair the member before relying on this input."
            if severity != "warning"
            else "Review only if this metadata is expected to carry required project content."
        ),
        exception=exc,
    )
    return {
        **diagnostic,
        # Compatibility fields retained for existing project-reader callers.
        "entry": entry,
        "kind": _object_kind(suffix) if suffix in XML_OBJECT_EXTS else suffix.lstrip("."),
        "parser": parser,
        "classification": classification,
        "severity": severity,
        "error": message,
    }


def _is_known_irrelevant_metadata(entry: str) -> bool:
    name = Path(entry).name.casefold()
    parts = {part.casefold() for part in PurePosixPath(entry).parts}
    return name in {"nodedescription.xml", "metadata.xml"} or "metadata" in parts


def _object_subtype_diagnostic(
    record: dict[str, Any], entry: str, suffix: str
) -> dict[str, Any] | None:
    if suffix != ".xml":
        return None
    if any(
        record.get(field)
        for field in ("object_name", "type_id", "functional_group", "names", "guids")
    ):
        return None
    if _is_known_irrelevant_metadata(entry):
        classification = "known_irrelevant_metadata"
        severity = "warning"
    else:
        classification = "unsupported_xml_subtype"
        severity = "error"
    code = (
        DiagnosticCode.PARSER_FAILED
        if classification == "known_irrelevant_metadata"
        else DiagnosticCode.ZEIA_SCHEMA_MALFORMED
    )
    record["inspection_status"] = "unsupported_subtype"
    record.setdefault("source_metadata", {})["classification"] = classification
    diagnostic = make_diagnostic(
        code,
        "unsupported XML object subtype preserved as a generic record",
        severity=severity,
        entry_path=entry,
        source_entity=entry,
        next_action=(
            "Review the object subtype if it is required by a source script."
            if severity != "warning"
            else "Review only if this metadata is expected to carry required project content."
        ),
    )
    return {
        **diagnostic,
        "entry": entry,
        "kind": _object_kind(suffix),
        "parser": "xml:generic",
        "classification": classification,
        "handling": "intentionally_unsupported",
        "severity": severity,
        "error": "unsupported XML object subtype preserved as a generic record",
    }
