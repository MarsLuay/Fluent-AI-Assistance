"""Local Tecan file readers."""

from .archive import inspect_archive
from .compare import compare_archives
from .gwl import inspect_gwl
from .pattern_library import mine_script_patterns, search_script_patterns, summarize_script_patterns
from .project_index import build_project_index, search_project_index, summarize_project_index
from .project_model import (
    CANONICAL_PROJECT_MODEL_SCHEMA_VERSION,
    CanonicalProjectModel,
    build_completeness_metadata,
    InspectionReport,
    SourceProvenance,
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
    "AdapterMatch",
    "ArchiveProbe",
    "CanonicalProjectModel",
    "DetectionResult",
    "build_completeness_metadata",
    "InspectionReport",
    "SourceProvenance",
    "ZeiaAdapter",
    "ZeiaFormatError",
    "build_project_index",
    "compare_archives",
    "detect_zeia_format",
    "ingest_zeia",
    "inspect_archive",
    "inspect_canonical_archive",
    "inspect_gwl",
    "inspect_xml_object",
    "inspect_xscr",
    "mine_script_patterns",
    "probe_zeia",
    "resolve_adapter",
    "search_project_index",
    "search_script_patterns",
    "summarize_project_index",
    "summarize_script_patterns",
]
