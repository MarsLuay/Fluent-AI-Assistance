"""Safe ZIP limits for ZEIA archive inspection and import."""

from __future__ import annotations

import zipfile


MAX_ZEIA_ENTRY_COUNT = 25_000
MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


def validate_zeia_archive_limits(
    archive: zipfile.ZipFile,
    *,
    max_entry_count: int = MAX_ZEIA_ENTRY_COUNT,
    max_total_uncompressed_bytes: int = MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
) -> list[zipfile.ZipInfo]:
    """Reject ZEIA archives that exceed the workspace safety envelope."""
    infos = archive.infolist()
    if len(infos) > max_entry_count:
        raise zipfile.BadZipFile(
            f"ZEIA archive exceeds safe entry count limit ({len(infos)} > {max_entry_count})"
        )
    total_uncompressed = sum(max(int(info.file_size), 0) for info in infos)
    if total_uncompressed > max_total_uncompressed_bytes:
        raise zipfile.BadZipFile(
            "ZEIA archive exceeds safe total uncompressed size limit "
            f"({total_uncompressed} > {max_total_uncompressed_bytes})"
        )
    return infos

