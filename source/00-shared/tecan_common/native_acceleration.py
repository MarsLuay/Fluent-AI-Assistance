"""Optional, fail-safe access to the compiled Rust acceleration kernels."""

from __future__ import annotations

import json
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


def _encode_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def worklist_execution_contract(commands: Any) -> list[dict[str, Any]] | None:
    """Build JSON-compatible Load/Execute contracts natively when available."""
    if not enabled():
        return None
    try:
        encoded = _encode_json(commands)
        result = json.loads(_native.worklist_execution_contract(encoded))
        return result if isinstance(result, list) else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_load_contract(
    fields: Any,
    command_id: str,
    command_version: str,
    provenance: Any,
) -> dict[str, Any] | None:
    """Normalize one JSON-compatible Load Worklist contract natively."""
    if not enabled():
        return None
    try:
        result = json.loads(
            _native.worklist_load_contract(
                _encode_json(fields),
                command_id,
                command_version,
                _encode_json(provenance),
            )
        )
        return result if isinstance(result, dict) else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_validate_transfers(
    transfers: Any, strict: bool
) -> dict[str, Any] | None:
    """Validate JSON-compatible transfer records with the native worklist core."""
    if not enabled():
        return None
    try:
        result = json.loads(
            _native.worklist_validate_transfers(_encode_json(transfers), strict)
        )
        if not isinstance(result, dict):
            return None
        if not all(
            isinstance(result.get(key), list)
            and all(isinstance(item, str) for item in result[key])
            for key in ("errors", "warnings")
        ):
            return None
        return result
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_well_to_position(value: str, rows: int) -> int | None:
    """Convert a supported well coordinate natively, or request Python fallback."""
    if not enabled():
        return None
    try:
        result = _native.worklist_well_to_position(value, rows)
        return result if type(result) is int and result >= 0 else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_load_transfers(
    path: str | Path, well_rows: int
) -> list[dict[str, Any]] | None:
    """Load transfer CSV records natively, or request the Python parser fallback."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_load_transfers(str(path), well_rows)
        if encoded is None:
            return None
        result = json.loads(encoded)
        if isinstance(result, list) and all(isinstance(item, dict) for item in result):
            return result
        return None
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_build_transfers(
    transfers: Any,
    name: str,
    wash_policy: str,
    batch_size: int | None,
) -> dict[str, Any] | None:
    """Assemble GWL text and ordered counts with the native worklist core."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_build_transfers(
            _encode_json(transfers), name, wash_policy, batch_size
        )
        if encoded is None:
            return None
        result = json.loads(encoded)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            return None
        counts = result.get("counts")
        order = result.get("count_order")
        if (
            not isinstance(counts, dict)
            or not all(isinstance(key, str) and type(value) is int for key, value in counts.items())
            or not isinstance(order, list)
            or not all(isinstance(key, str) for key in order)
            or len(order) != len(set(order))
            or set(order) != set(counts)
        ):
            return None
        return {
            "text": result["text"],
            "counts": {key: counts[key] for key in order},
        }
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_parse_gwl(path: str | Path) -> dict[str, Any] | None:
    """Parse a strict GWL file natively, or request the Python fallback."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_parse_gwl(str(path))
        if encoded is None:
            return None
        result = json.loads(encoded)
        if not isinstance(result, dict):
            return None
        counts = result.get("counts")
        order = result.get("count_order")
        if (
            not isinstance(counts, dict)
            or not all(isinstance(key, str) and type(value) is int for key, value in counts.items())
            or not isinstance(order, list)
            or not all(isinstance(key, str) for key in order)
            or len(order) != len(set(order))
            or set(order) != set(counts)
        ):
            return None
        ordered_counts = {key: counts[key] for key in order}
        return {
            "counts": ordered_counts,
            "record_count": sum(ordered_counts.values()),
        }
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_analyze_lines(
    lines: Any,
    enabled_tip_mask: str | None,
    supported_channels: int,
) -> dict[str, Any] | None:
    """Analyze normalized GWL lines with the native semantic engine."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_analyze_lines(
            _encode_json(lines), enabled_tip_mask, supported_channels
        )
        if encoded is None:
            return None
        result = json.loads(encoded)
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return None
        if not all(
            isinstance(result.get(key), str)
            for key in ("schema_version", "runtime_parallelism")
        ):
            return None
        if not all(
            type(result.get(key)) is int
            for key in ("record_count", "unit_count", "automatic_tip_units")
        ):
            return None
        explicit = result.get("explicit_tip_assignments")
        if not isinstance(explicit, dict) or not all(
            isinstance(key, str) and type(value) is int
            for key, value in explicit.items()
        ):
            return None
        if not all(
            isinstance(result.get(key), list)
            for key in ("barriers", "parallel_windows", "diagnostics")
        ):
            return None
        if result.get("enabled_tip_mask") is not None and type(
            result["enabled_tip_mask"]
        ) is not int:
            return None
        return result
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_optimize_lines(
    lines: Any,
    name: str,
    enabled_tip_mask: str | None,
    supported_channels: int,
) -> dict[str, Any] | None:
    """Optimize normalized GWL lines with the native semantic engine."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_optimize_lines(
            _encode_json(lines), name, enabled_tip_mask, supported_channels
        )
        if encoded is None:
            return None
        result = json.loads(encoded)
        if (
            not isinstance(result, dict)
            or type(result.get("ok")) is not bool
            or not isinstance(result.get("text"), str)
            or type(result.get("changed")) is not bool
            or not isinstance(result.get("reordered_units"), list)
            or not all(
                isinstance(unit, list)
                and all(type(index) is int for index in unit)
                for unit in result["reordered_units"]
            )
            or not isinstance(result.get("reasons"), list)
            or not all(isinstance(reason, str) for reason in result["reasons"])
        ):
            return None
        return result
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None
