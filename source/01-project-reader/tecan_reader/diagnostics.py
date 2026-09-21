"""Stable, JSON-safe diagnostics for ZEIA ingestion and readiness."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping
import zipfile


DIAGNOSTIC_SCHEMA_VERSION = "tecan.ingestion_diagnostic.v1"
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key)\b\s*[:=]\s*([^\s,;]+)"
)


def _redact_text(value: str) -> str:
    return _SENSITIVE_VALUE_RE.sub(r"\1=<redacted>", value)


class IngestionArchiveError(zipfile.BadZipFile):
    """A legacy-compatible archive failure carrying a structured diagnostic."""

    def __init__(self, message: str, diagnostic: Mapping[str, Any]):
        super().__init__(message)
        self.diagnostic = dict(diagnostic)


class DiagnosticCode:
    """Stable codes exposed to CLI, MCP, and automation callers."""

    INPUT_NOT_FOUND = "INPUT_NOT_FOUND"
    INPUT_UNREADABLE = "INPUT_UNREADABLE"
    ARCHIVE_INVALID = "ARCHIVE_INVALID"
    ZEIA_UNKNOWN_FORMAT = "ZEIA_UNKNOWN_FORMAT"
    ZEIA_AMBIGUOUS_FORMAT = "ZEIA_AMBIGUOUS_FORMAT"
    ZEIA_SCHEMA_MALFORMED = "ZEIA_SCHEMA_MALFORMED"
    DEPENDENCY_UNRESOLVED = "DEPENDENCY_UNRESOLVED"
    DEPENDENCY_AMBIGUOUS = "DEPENDENCY_AMBIGUOUS"
    REFERENCE_UNRESOLVED = "REFERENCE_UNRESOLVED"
    REFERENCE_AMBIGUOUS = "REFERENCE_AMBIGUOUS"
    ENCODING_DECODE_FAILED = "ENCODING_DECODE_FAILED"
    PARSER_FAILED = "PARSER_FAILED"
    LIMIT_ENTRY_COUNT = "LIMIT_ENTRY_COUNT"
    LIMIT_UNCOMPRESSED_BYTES = "LIMIT_UNCOMPRESSED_BYTES"
    LIMIT_MEMBER_BYTES = "LIMIT_MEMBER_BYTES"
    LIMIT_COMPRESSION_RATIO = "LIMIT_COMPRESSION_RATIO"
    ARCHIVE_DUPLICATE_MEMBER = "ARCHIVE_DUPLICATE_MEMBER"
    ARCHIVE_UNSAFE_PATH = "ARCHIVE_UNSAFE_PATH"
    REQUEST_INVALID = "REQUEST_INVALID"


@dataclass(frozen=True)
class IngestionDiagnostic:
    """One deterministic, provenance-carrying ingestion diagnostic."""

    code: str
    severity: str
    message: str
    archive_path: str | None = None
    entry_path: str | None = None
    adapter_id: str | None = None
    source_entity: str | None = None
    reference: str | None = None
    limit: int | float | str | None = None
    value: int | float | str | None = None
    compressed_size: int | None = None
    uncompressed_size: int | None = None
    violated_limit: str | None = None
    next_action: str | None = None
    exception_type: str | None = None
    exception_detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        fields = (
            "archive_path", "entry_path", "adapter_id", "source_entity", "reference",
            "limit", "value", "compressed_size", "uncompressed_size",
            "violated_limit", "next_action", "exception_type", "exception_detail",
        )
        for field in fields:
            value = getattr(self, field)
            if value not in (None, ""):
                payload[field] = value
        return payload


def make_diagnostic(
    code: str,
    message: str,
    *,
    severity: str = "error",
    archive_path: str | None = None,
    entry_path: str | None = None,
    adapter_id: str | None = None,
    source_entity: str | None = None,
    reference: str | None = None,
    limit: int | float | str | None = None,
    value: int | float | str | None = None,
    compressed_size: int | None = None,
    uncompressed_size: int | None = None,
    violated_limit: str | None = None,
    next_action: str | None = None,
    exception: BaseException | None = None,
) -> dict[str, Any]:
    """Create a public record while keeping exception detail secondary."""
    return IngestionDiagnostic(
        code=code,
        severity=severity,
        message=_redact_text(message),
        archive_path=archive_path,
        entry_path=entry_path,
        adapter_id=adapter_id,
        source_entity=source_entity,
        reference=reference,
        limit=limit,
        value=value,
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
        violated_limit=violated_limit,
        next_action=next_action,
        exception_type=type(exception).__name__ if exception is not None else None,
        exception_detail=_redact_text(str(exception)) if exception is not None else None,
    ).to_dict()


def sort_diagnostics(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return stable copies sorted by provenance and code."""
    return sorted(
        [dict(item) for item in items],
        key=lambda item: tuple(str(item.get(key) or "") for key in (
            "archive_path", "entry_path", "code", "severity", "message",
        )),
    )


def diagnostic_message(item: Mapping[str, Any]) -> str:
    """Provide the legacy human-readable text used by compatibility callers."""
    return str(item.get("message") or item.get("code") or "ingestion diagnostic")


__all__ = [
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DiagnosticCode",
    "IngestionDiagnostic",
    "IngestionArchiveError",
    "diagnostic_message",
    "make_diagnostic",
    "sort_diagnostics",
]
