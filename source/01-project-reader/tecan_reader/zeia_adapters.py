"""Structural ZEIA probes and adapters for the canonical project model."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
import zipfile

from tecan_common.xml_compat import MAX_XML_BYTES
from tecan_common.zeia_limits import (
    MAX_ZEIA_ENTRY_COUNT,
    MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    validate_zeia_archive_limits,
)

from .common import extension_counts
from .gwl import inspect_gwl_lines
from .project_model import (
    CanonicalProjectModel,
    InspectionReport,
    build_completeness_metadata,
    normalize_record,
)
from .script import inspect_xscr_text
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

    @property
    def selected(self) -> AdapterMatch | None:
        if self.status != "supported" or not self.matches:
            return None
        return sorted(self.matches, key=lambda item: (-item.confidence, item.adapter_id))[0]

    def to_dict(self) -> dict[str, Any]:
        selected = self.selected
        return {
            "status": self.status,
            "entries": list(self.entries),
            "matches": [match.to_dict() for match in self.matches],
            "selected_adapter": selected.adapter_id if selected else None,
            "format_family": selected.format_family if selected else None,
            "diagnostics": list(self.diagnostics),
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
        return AdapterMatch(self.adapter_id, self.format_family, 0.7, False, tuple(evidence))


XML_OBJECT_EXTS = {".xcmp", ".xwsp", ".xlqc", ".xlcp", ".xsit", ".xcon", ".xml"}
ASSET_EXTS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}


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
) -> DetectionResult:
    """Probe archive structure without guessing from the filename/extension."""

    archive_path = Path(path).expanduser().resolve()
    if not archive_path.exists():
        return DetectionResult("unsupported", (), (), (f"archive not found: {archive_path}",))
    if not zipfile.is_zipfile(archive_path):
        return DetectionResult("unsupported", (), (), (f"not a readable ZIP archive: {archive_path}",))
    try:
        with zipfile.ZipFile(archive_path) as zf:
            infos = validate_zeia_archive_limits(
                zf,
                max_entry_count=max_entry_count,
                max_total_uncompressed_bytes=max_total_uncompressed_bytes,
            )
            archive = _build_probe(zf, infos)
    except zipfile.BadZipFile:
        # Safety-limit failures are part of the established reader contract and
        # must remain distinguishable from an unsupported export structure.
        raise
    except (OSError, ValueError) as exc:
        return DetectionResult(
            "unsupported",
            (),
            (),
            (f"archive safety/format validation failed: {exc}",),
        )

    matches = tuple(match for adapter in ADAPTERS if (match := adapter.probe(archive)) is not None)
    strong = tuple(match for match in matches if match.strong)
    if len(strong) > 1:
        diagnostics = tuple(
            f"{match.adapter_id} matched: {', '.join(match.evidence)}"
            for match in strong
        )
        return DetectionResult("ambiguous", archive.entries, matches, diagnostics)
    if not matches:
        diagnostics = _unsupported_diagnostics(archive)
        return DetectionResult("unsupported", archive.entries, (), diagnostics)
    return DetectionResult("supported", archive.entries, matches)


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
) -> CanonicalProjectModel:
    """Normalize one complete export through the selected adapter."""

    archive_path = Path(path).expanduser().resolve()
    detection = probe_zeia(
        archive_path,
        max_entry_count=max_entry_count,
        max_total_uncompressed_bytes=max_total_uncompressed_bytes,
    )
    if detection.status != "supported":
        raise ZeiaFormatError(detection)
    selected = detection.selected
    if selected is None:  # pragma: no cover - guarded by DetectionResult
        raise ZeiaFormatError(DetectionResult("unsupported", detection.entries, (), ("no adapter selected",)))

    errors: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    worklists: list[dict[str, Any]] = []
    eligible_member_counts = _eligible_member_counts(detection.entries)
    oversized_members: list[str] = []
    try:
        with zipfile.ZipFile(archive_path) as zf:
            infos = validate_zeia_archive_limits(
                zf,
                max_entry_count=max_entry_count,
                max_total_uncompressed_bytes=max_total_uncompressed_bytes,
            )
            eligible_member_counts = _eligible_member_counts(
                [info.filename for info in infos]
            )
            oversized_members = [
                _normal_entry(info.filename)
                for info in infos
                if not info.is_dir()
                and info.file_size > MAX_XML_BYTES
                and Path(info.filename).suffix.casefold() in {".xscr", *XML_OBJECT_EXTS}
                and not _is_known_irrelevant_metadata(_normal_entry(info.filename))
            ]
            for info in infos:
                if info.is_dir():
                    continue
                entry = _normal_entry(info.filename)
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
                    except Exception as exc:
                        errors.append(
                            _parse_error(entry, exc, suffix=suffix, parser="xscr")
                        )
                elif suffix == ".gwl":
                    try:
                        text = zf.read(info).decode("utf-8-sig")
                        worklists.append(
                            normalize_record(
                                inspect_gwl_lines(text.splitlines(), source_name=entry),
                                kind="worklist",
                                source_archive=archive_path,
                            )
                        )
                    except Exception as exc:
                        errors.append(
                            _parse_error(entry, exc, suffix=suffix, parser="gwl")
                        )
                elif suffix in XML_OBJECT_EXTS:
                    if object_limit is not None and len(objects) >= object_limit:
                        continue
                    try:
                        record = normalize_record(
                            _inspect_object_member(
                                _read_member_data(zf, info),
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
                    except Exception as exc:
                        # Preserve every failed member as a classified diagnostic;
                        # completeness and readiness decide whether it is blocking.
                        errors.append(
                            _parse_error(
                                entry,
                                exc,
                                suffix=suffix,
                                parser=f"xml:{suffix.lstrip('.')}",
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
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(_parse_error("<archive>", exc, suffix=".zeia", parser="archive"))

    summarized_counts = {
        "scripts": len(scripts),
        "objects": len(objects),
        "worklists": len(worklists),
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
        "adapter_matches": [match.to_dict() for match in detection.matches],
        "completeness": completeness,
        "complete": completeness["complete"],
        "eligible_member_counts": completeness["eligible_member_counts"],
        "summarized_counts": completeness["summarized_counts"],
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
    )


def inspect_canonical_archive(path: str | Path, **kwargs: Any) -> InspectionReport:
    """Return the legacy report view while retaining its canonical model."""
    return InspectionReport(ingest_zeia(path, **kwargs))


def _build_probe(zf: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> ArchiveProbe:
    entries = tuple(_normal_entry(info.filename) for info in infos)
    samples: list[tuple[str, str]] = []
    for info in infos:
        if info.is_dir() or Path(info.filename).suffix.casefold() not in {
            ".xscr", ".xcmp", ".xwsp", ".xlqc", ".xlcp", ".xsit", ".xcon", ".xml"
        }:
            continue
        if len(samples) >= 32:
            break
        try:
            data = _read_prefix(zf, info, 128 * 1024)
            samples.append(
                (_normal_entry(info.filename), data.decode("utf-8-sig", errors="replace"))
            )
        except (OSError, RuntimeError, zipfile.BadZipFile):
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
    worklist_samples = 0
    for info in infos:
        if info.is_dir() or Path(info.filename).suffix.casefold() != ".gwl":
            continue
        if worklist_samples >= 32:
            break
        worklist_samples += 1
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
        has_object_structure=has_object_structure,
    )


def _unsupported_diagnostics(archive: ArchiveProbe) -> tuple[str, ...]:
    if archive.versions:
        return (f"unsupported dataStoreVersion value(s): {', '.join(archive.versions)}",)
    if not archive.entries:
        return ("archive has no file entries",)
    return ("no supported ZEIA structural evidence (expected script or catalog XML)",)


def _normal_entry(value: str) -> str:
    normalized = str(value or "").replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"unsafe archive entry path: {value}")
    return pure.as_posix()


def _read_prefix(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    with zf.open(info) as stream:
        return stream.read(limit)


def _read_member_data(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size <= MAX_XML_BYTES:
        return zf.read(info)
    return _read_prefix(zf, info, MAX_XML_BYTES + 1)


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
    }


def _parse_error(
    entry: str,
    exc: Exception,
    *,
    suffix: str,
    parser: str,
) -> dict[str, str]:
    if _is_known_irrelevant_metadata(entry):
        classification = "known_irrelevant_metadata"
        severity = "warning"
    elif isinstance(exc, (OSError, EOFError, RuntimeError, zipfile.BadZipFile)):
        classification = "unreadable_or_encoding_failure"
        severity = "error"
    elif isinstance(exc, UnicodeDecodeError):
        classification = "unreadable_or_encoding_failure"
        severity = "error"
    elif type(exc).__name__.lower().endswith("parseerror"):
        classification = "malformed"
        severity = "error"
    else:
        classification = "parser_failure"
        severity = "error"
    return {
        "entry": entry,
        "kind": _object_kind(suffix) if suffix in XML_OBJECT_EXTS else suffix.lstrip("."),
        "parser": parser,
        "classification": classification,
        "severity": severity,
        "error": f"{type(exc).__name__}: {exc}",
    }


def _is_known_irrelevant_metadata(entry: str) -> bool:
    name = Path(entry).name.casefold()
    parts = {part.casefold() for part in PurePosixPath(entry).parts}
    return name in {"nodedescription.xml", "metadata.xml"} or "metadata" in parts


def _object_subtype_diagnostic(
    record: dict[str, Any], entry: str, suffix: str
) -> dict[str, str] | None:
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
    record["inspection_status"] = "unsupported_subtype"
    record.setdefault("source_metadata", {})["classification"] = classification
    return {
        "entry": entry,
        "kind": _object_kind(suffix),
        "parser": "xml:generic",
        "classification": classification,
        "handling": "intentionally_unsupported",
        "severity": severity,
        "error": "unsupported XML object subtype preserved as a generic record",
    }
