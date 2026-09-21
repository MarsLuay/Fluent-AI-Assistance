"""Reader for `.zeia` FluentControl export archives."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tecan_common.zeia_limits import (
    MAX_ZEIA_COMPRESSION_RATIO,
    MAX_ZEIA_ENTRY_COUNT,
    MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES,
    MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
)

from .project_model import InspectionReport
from .zeia_adapters import ingest_zeia


# Kept public for callers that used the legacy inspection constants.
XML_OBJECT_EXTS = {".xcmp", ".xwsp", ".xlqc", ".xlcp", ".xsit", ".xcon", ".xml"}
ASSET_EXTS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}


def inspect_archive(
    path: str | Path,
    *,
    script_limit: int | None = 50,
    object_limit: int | None = 200,
    max_entry_count: int = MAX_ZEIA_ENTRY_COUNT,
    max_total_uncompressed_bytes: int = MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    max_member_uncompressed_bytes: int | None = MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = MAX_ZEIA_COMPRESSION_RATIO,
) -> dict[str, Any]:
    """Return a compatibility inspection view backed by canonical ingestion."""
    model = ingest_zeia(
        path,
        script_limit=script_limit,
        object_limit=object_limit,
        max_entry_count=max_entry_count,
        max_total_uncompressed_bytes=max_total_uncompressed_bytes,
        max_member_uncompressed_bytes=max_member_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
    )
    return InspectionReport(model)
