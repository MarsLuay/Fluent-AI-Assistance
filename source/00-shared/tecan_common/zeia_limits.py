"""Shared resource and path safety limits for ZEIA ZIP archives."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import PurePosixPath
import unicodedata
import zipfile


MAX_ZEIA_ENTRY_COUNT = 25_000
MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
# XML parsing has a smaller in-memory preview budget. This is the hard
# container-level bound used before any member can be materialized or written.
MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
# Keep this high enough for ordinary highly repetitive XML exports while still
# making classic ZIP-bomb ratios configurable and rejectable.
MAX_ZEIA_COMPRESSION_RATIO = 10_000.0


class ZeiaArchiveValidationError(zipfile.BadZipFile):
    """A ZIP validation failure carrying the member and limit metadata."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        entry_name: str | None = None,
        previous_entry_name: str | None = None,
        compressed_size: int | None = None,
        uncompressed_size: int | None = None,
        limit: int | float | None = None,
        normalized_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.entry_name = entry_name
        self.previous_entry_name = previous_entry_name
        self.compressed_size = compressed_size
        self.uncompressed_size = uncompressed_size
        self.limit = limit
        self.normalized_name = normalized_name


@dataclass(frozen=True)
class ZeiaArchiveMember:
    """One validated ZIP member and its canonical portable archive name."""

    info: zipfile.ZipInfo
    name: str


@dataclass(frozen=True)
class ZeiaArchiveInventory:
    """Validated metadata that can be reused while an archive is open."""

    members: tuple[ZeiaArchiveMember, ...]
    total_uncompressed_bytes: int

    @property
    def infos(self) -> tuple[zipfile.ZipInfo, ...]:
        return tuple(member.info for member in self.members)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(member.name for member in self.members)


def normalize_zeia_member_name(value: str) -> str:
    """Return one safe, portable archive name or raise ``ValueError``.

    ZIP names are logical POSIX paths even when an exporter writes Windows
    separators. Normalize both separators for lookup, but reject absolute,
    drive-qualified, traversal, and NUL-containing names before any filesystem
    path is constructed.
    """
    raw = str(value or "")
    if "\x00" in raw:
        raise ValueError(f"unsafe archive entry path contains NUL: {value!r}")
    normalized = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    pure = PurePosixPath(normalized)
    if (
        normalized.startswith("/")
        or pure.is_absolute()
        or re.match(r"^[A-Za-z]:($|/)", normalized)
    ):
        raise ValueError(f"unsafe archive entry path: {value}")
    parts = tuple(part for part in pure.parts if part not in {"", "."})
    if any(part == ".." for part in parts):
        raise ValueError(f"unsafe archive entry path: {value}")
    return "/".join(parts)


def build_zeia_archive_inventory(
    archive: zipfile.ZipFile,
    *,
    max_entry_count: int = MAX_ZEIA_ENTRY_COUNT,
    max_total_uncompressed_bytes: int = MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    max_member_uncompressed_bytes: int | None = MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = MAX_ZEIA_COMPRESSION_RATIO,
) -> ZeiaArchiveInventory:
    """Validate archive metadata once and return reusable member inventory."""
    infos = tuple(archive.infolist())
    if len(infos) > max_entry_count:
        raise ZeiaArchiveValidationError(
            "ZEIA archive exceeds safe entry count limit "
            f"({len(infos)} > {max_entry_count})",
            reason="entry_count",
            limit=max_entry_count,
            uncompressed_size=len(infos),
        )

    members: list[ZeiaArchiveMember] = []
    seen: dict[str, str] = {}
    total_uncompressed = 0
    for info in infos:
        try:
            name = normalize_zeia_member_name(info.filename)
        except ValueError as exc:
            raise ZeiaArchiveValidationError(
                str(exc),
                reason="unsafe_path",
                entry_name=str(info.filename),
                compressed_size=max(int(info.compress_size), 0),
                uncompressed_size=max(int(info.file_size), 0),
            ) from exc

        duplicate_key = name.casefold()
        previous = seen.get(duplicate_key)
        if previous is not None:
            raise ZeiaArchiveValidationError(
                "ZEIA archive contains duplicate normalized member path "
                f"{name!r} ({previous!r} and {info.filename!r})",
                reason="duplicate_member",
                entry_name=str(info.filename),
                previous_entry_name=previous,
                compressed_size=max(int(info.compress_size), 0),
                uncompressed_size=max(int(info.file_size), 0),
                normalized_name=name,
            )
        seen[duplicate_key] = str(info.filename)

        compressed_size = max(int(info.compress_size), 0)
        uncompressed_size = max(int(info.file_size), 0)
        if (
            max_member_uncompressed_bytes is not None
            and uncompressed_size > max_member_uncompressed_bytes
        ):
            raise ZeiaArchiveValidationError(
                "ZEIA archive member exceeds safe uncompressed size limit "
                f"({name}: {uncompressed_size} > {max_member_uncompressed_bytes}; "
                f"compressed={compressed_size})",
                reason="member_uncompressed_bytes",
                entry_name=str(info.filename),
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                limit=max_member_uncompressed_bytes,
                normalized_name=name,
            )
        if (
            max_compression_ratio is not None
            and uncompressed_size > 0
            and uncompressed_size / max(compressed_size, 1) > max_compression_ratio
        ):
            ratio = uncompressed_size / max(compressed_size, 1)
            raise ZeiaArchiveValidationError(
                "ZEIA archive member exceeds safe compression ratio limit "
                f"({name}: {ratio:.2f} > {max_compression_ratio}; "
                f"compressed={compressed_size}, uncompressed={uncompressed_size})",
                reason="compression_ratio",
                entry_name=str(info.filename),
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                limit=max_compression_ratio,
                normalized_name=name,
            )
        total_uncompressed += uncompressed_size
        members.append(ZeiaArchiveMember(info=info, name=name))

    if total_uncompressed > max_total_uncompressed_bytes:
        raise ZeiaArchiveValidationError(
            "ZEIA archive exceeds safe total uncompressed size limit "
            f"({total_uncompressed} > {max_total_uncompressed_bytes})",
            reason="total_uncompressed_bytes",
            limit=max_total_uncompressed_bytes,
            uncompressed_size=total_uncompressed,
        )
    return ZeiaArchiveInventory(tuple(members), total_uncompressed)


def validate_zeia_archive_limits(
    archive: zipfile.ZipFile,
    *,
    max_entry_count: int = MAX_ZEIA_ENTRY_COUNT,
    max_total_uncompressed_bytes: int = MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    max_member_uncompressed_bytes: int | None = MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = MAX_ZEIA_COMPRESSION_RATIO,
) -> list[zipfile.ZipInfo]:
    """Reject unsafe ZEIA metadata while preserving the legacy list result."""
    return list(
        build_zeia_archive_inventory(
            archive,
            max_entry_count=max_entry_count,
            max_total_uncompressed_bytes=max_total_uncompressed_bytes,
            max_member_uncompressed_bytes=max_member_uncompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        ).infos
    )


__all__ = [
    "MAX_ZEIA_COMPRESSION_RATIO",
    "MAX_ZEIA_ENTRY_COUNT",
    "MAX_ZEIA_MEMBER_UNCOMPRESSED_BYTES",
    "MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES",
    "ZeiaArchiveInventory",
    "ZeiaArchiveMember",
    "ZeiaArchiveValidationError",
    "build_zeia_archive_inventory",
    "normalize_zeia_member_name",
    "validate_zeia_archive_limits",
]
