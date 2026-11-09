"""Optional, fail-safe access to the compiled Rust acceleration kernels."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from tecan_native import _native
except (ImportError, OSError):  # pragma: no cover - depends on optional wheel
    _native = None


def enabled() -> bool:
    """Return whether the extension is present and not explicitly disabled."""
    disabled = os.environ.get("TECAN_DISABLE_RUST_ACCELERATION", "").strip().lower()
    return _native is not None and disabled not in {"1", "true", "yes", "on"}


def available() -> bool:
    """Report availability without changing the active feature flag."""
    return _native is not None


def parse_xml(data: bytes, max_bytes: int, max_depth: int) -> Any | None:
    """Try native XML parsing; callers retain the Python parser as authority."""
    if not enabled():
        return None
    try:
        return _native.parse_xml(data, max_bytes, max_depth)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        # Unsupported encodings and parser edge cases deliberately fall back.
        return None


def deepcopy_json(value: Any) -> Any | None:
    """Clone JSON-like containers natively, or request ``copy.deepcopy`` fallback."""
    if not enabled():
        return None
    try:
        return _native.deepcopy_json(value)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def canonical_json(value: Any) -> str | None:
    """Serialize JSON-compatible values with sorted keys, or request fallback."""
    if not enabled():
        return None
    try:
        return _native.canonical_json(value)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def catalog_source_paths(install_path: str | Path, include_all_connectors: bool) -> list[str] | None:
    if not enabled():
        return None
    try:
        return _native.catalog_source_paths(str(install_path), include_all_connectors)
    except (OSError, ValueError, TypeError):
        return None


def catalog_stat_fingerprint(install_path: str | Path, include_all_connectors: bool) -> str | None:
    if not enabled():
        return None
    try:
        return _native.catalog_stat_fingerprint(str(install_path), include_all_connectors)
    except (OSError, ValueError, TypeError):
        return None


def source_content_fingerprint(path: str | Path, install_path: str | Path) -> str | None:
    if not enabled():
        return None
    try:
        return _native.source_content_fingerprint(str(path), str(install_path))
    except (OSError, ValueError, TypeError):
        return None


def catalog_content_fingerprints(
    install_path: str | Path, include_all_connectors: bool
) -> dict[str, str] | None:
    if not enabled():
        return None
    try:
        return dict(_native.catalog_content_fingerprints(str(install_path), include_all_connectors))
    except (OSError, ValueError, TypeError):
        return None


def xscr_object_fields(root: Any) -> dict[Any, dict[str, dict[str, Any]]] | None:
    """Index bounded XSCR Object fields without changing Python IR conversion."""
    if not enabled():
        return None
    try:
        return dict(_native.xscr_object_fields(root))
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def pattern_windows(
    scripts: list[dict[str, Any]],
    classifications: list[list[list[str]]],
    context_before: int,
    context_after: int,
) -> list[dict[str, Any]] | None:
    if not enabled():
        return None
    try:
        return _native.pattern_windows(scripts, classifications, context_before, context_after)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None
