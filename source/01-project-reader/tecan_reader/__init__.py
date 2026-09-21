"""Local Tecan file readers."""

from .archive import inspect_archive
from .compare import compare_archives
from .gwl import inspect_gwl
from .pattern_library import mine_script_patterns, search_script_patterns, summarize_script_patterns
from .project_index import (
    build_project_index,
    find_entity_candidates,
    search_project_index,
    summarize_project_index,
)
from .project_model import (
    CANONICAL_PROJECT_MODEL_SCHEMA_VERSION,
    CanonicalProjectModel,
    build_completeness_metadata,
    InspectionReport,
    SourceProvenance,
)
from .full_export_readiness import (
    FullExportReadiness,
    IdentifierConflict,
    ReadinessStatus,
    UnresolvedReference,
    resolve_full_export_readiness,
    resolve_manifest_readiness,
)
from .diagnostics import (
    DIAGNOSTIC_SCHEMA_VERSION,
    DiagnosticCode,
    IngestionArchiveError,
    IngestionDiagnostic,
    make_diagnostic,
    sort_diagnostics,
)
from .script import inspect_xscr
from .xmlobj import inspect_xml_object
from .zeia_adapters import (
    AdapterMatch,
    ArchiveProbe,
    DetectionResult,
    ZeiaAdapter,
    ZeiaFormatError,
    detect_zeia_format,
    ingest_zeia,
    inspect_canonical_archive,
    probe_zeia,
    resolve_adapter,
)

__all__ = [
    "CANONICAL_PROJECT_MODEL_SCHEMA_VERSION",
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DiagnosticCode",
    "AdapterMatch",
    "ArchiveProbe",
    "CanonicalProjectModel",
    "FullExportReadiness",
    "IngestionArchiveError",
    "IngestionDiagnostic",
    "IdentifierConflict",
    "DetectionResult",
    "build_completeness_metadata",
    "InspectionReport",
    "ReadinessStatus",
    "SourceProvenance",
    "UnresolvedReference",
    "ZeiaAdapter",
    "ZeiaFormatError",
    "build_project_index",
    "find_entity_candidates",
    "compare_archives",
    "detect_zeia_format",
    "ingest_zeia",
    "inspect_archive",
    "inspect_canonical_archive",
    "inspect_gwl",
    "inspect_xml_object",
    "inspect_xscr",
    "mine_script_patterns",
    "make_diagnostic",
    "probe_zeia",
    "resolve_adapter",
    "resolve_full_export_readiness",
    "resolve_manifest_readiness",
    "sort_diagnostics",
    "search_project_index",
    "search_script_patterns",
    "summarize_project_index",
    "summarize_script_patterns",
]
