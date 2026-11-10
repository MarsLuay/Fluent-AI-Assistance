"""Optional, fail-safe access to the compiled Rust acceleration kernels."""

from __future__ import annotations

from collections import (
    Counter as _Counter,
    OrderedDict as _OrderedDict,
    defaultdict as _DefaultDict,
)
import json
import os
from pathlib import Path
from typing import Any

_PathType = type(Path())

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


def xml_local_name(tag: Any) -> str | None:
    """Return the namespace-free exact-string tag name natively when enabled."""
    if not enabled() or type(tag) is not str:
        return None
    try:
        result = _native.xml_tag_local_name(tag)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def xml_clean_text(value: Any) -> str | None:
    """Trim supported ASCII XML text natively, preserving Python fallback cases."""
    if not enabled():
        return None
    if value is None:
        text = ""
    elif (
        type(value) is str
        and value.isascii()
        and not any(0x1C <= ord(character) <= 0x1F for character in value)
    ):
        text = value
    else:
        return None
    try:
        result = _native.xml_clean_text(text)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def xml_unique_texts(
    root: Any, names: Any, limit: Any, element_type: Any
) -> list[str] | None:
    """Collect supported standard-Element text natively; otherwise use Python."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(names) not in (set, frozenset, list, tuple)
        or not all(type(name) is str for name in names)
        or type(limit) is not int
        or not -(1 << 63) <= limit < (1 << 63)
    ):
        return None
    try:
        result = _native.xml_unique_texts(root, list(names), limit, element_type)
        if result is None:
            return None
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_texts_by_name(root: Any, names: Any, element_type: Any) -> list[list[str]] | None:
    """Collect grouped supported XML text natively; otherwise use Python."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(names) not in (list, tuple)
        or not all(type(name) is str for name in names)
        or len(set(names)) != len(names)
    ):
        return None
    try:
        result = _native.xml_texts_by_name(root, list(names), element_type)
        if (
            type(result) is list
            and len(result) == len(names)
            and all(
                type(group) is list and all(type(value) is str for value in group)
                for group in result
            )
        ):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_find_text(
    root: Any, name: Any, direct: Any, element_type: Any
) -> tuple[bool, str | None] | None:
    """Find first supported XML text value natively, retaining Python fallback."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(name) is not str
        or type(direct) is not bool
    ):
        return None
    try:
        result = _native.xml_find_text(root, name, direct, element_type)
        if (
            type(result) is list
            and len(result) == 2
            and type(result[0]) is bool
            and ((result[0] and type(result[1]) is str) or (not result[0] and result[1] is None))
        ):
            return result[0], result[1]
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def render_append_list(title: Any, values: Any, limit: Any) -> list[str] | None:
    """Format supported list-report sections natively, otherwise request fallback."""
    if (
        not enabled()
        or type(title) is not str
        or type(values) not in (list, tuple)
        or not all(type(value) is str for value in values)
        or type(limit) is not int
        or not -(1 << 63) <= limit < (1 << 63)
    ):
        return None
    try:
        result = _native.render_append_list(title, list(values), limit)
        if type(result) is list and all(type(line) is str for line in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def decode_text_bytes(data: Any) -> str | None:
    """Decode the reader's supported text encodings natively when possible."""
    if not enabled() or type(data) is not bytes:
        return None
    try:
        result = _native.decode_text_bytes(data)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def read_text_file(path: str | Path) -> str | None:
    """Read and decode a text file natively, or request the Python fallback."""
    if not enabled() or not isinstance(path, (str, Path)):
        return None
    try:
        result = _native.read_text_file(str(path))
        return result if type(result) is str else None
    except (AttributeError, OSError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def read_utf8_sig_file(path: str | Path) -> str | None:
    """Read strict UTF-8 with BOM removal and universal newlines, or fall back."""
    if not enabled() or not isinstance(path, (str, Path)):
        return None
    try:
        result = _native.read_utf8_sig_file(str(path))
        return result if type(result) is str else None
    except (AttributeError, OSError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def to_jsonable_containers(value: Any) -> tuple[bool, Any] | None:
    """Convert supported exact JSON containers natively, or request Python fallback."""
    if not enabled():
        return None
    try:
        supported, result = _native.to_jsonable_containers(
            value, _Counter, _PathType, _DefaultDict, _OrderedDict
        )
        if type(supported) is bool and supported:
            return True, result
        return None
    except (AttributeError, TypeError, ValueError, OverflowError, UnicodeError):
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


def compact_list(values: Any, limit: Any = 20) -> list[str] | None:
    """Deduplicate exact string sequences natively, preserving order and limit rules."""
    if (
        not enabled()
        or type(values) not in (list, tuple)
        or not all(type(value) is str for value in values)
        or type(limit) is not int
    ):
        return None
    try:
        result = _native.compact_list(list(values), limit)
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, OverflowError, UnicodeError):
        return None


def extension_counts(suffixes: Any) -> dict[str, int] | None:
    """Count exact string suffixes natively and return them in sorted order."""
    if (
        not enabled()
        or type(suffixes) not in (list, tuple)
        or not all(type(suffix) is str for suffix in suffixes)
    ):
        return None
    try:
        pairs = _native.extension_counts(list(suffixes))
        if type(pairs) is not list:
            return None
        counts: dict[str, int] = {}
        for pair in pairs:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not int
                or pair[1] < 0
                or pair[0] in counts
            ):
                return None
            counts[pair[0]] = pair[1]
        return counts
    except (AttributeError, TypeError, ValueError, OverflowError, UnicodeError):
        return None


def diagnostic_sort_indices(keys: Any) -> list[int] | None:
    """Return stable native ordering for pre-normalized five-string keys."""
    if (
        not enabled()
        or type(keys) not in (list, tuple)
        or not all(
            type(key) in (list, tuple)
            and len(key) == 5
            and all(type(value) is str for value in key)
            for key in keys
        )
    ):
        return None
    try:
        encoded = _native.diagnostic_sort_indices(_encode_json(keys))
        if not isinstance(encoded, str):
            return None
        order = json.loads(encoded)
        if (
            type(order) is list
            and all(type(index) is int for index in order)
            and sorted(order) == list(range(len(keys)))
        ):
            return order
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def diagnostic_message(message: Any, code: Any) -> str | None:
    """Select a diagnostic's exact-string message or code natively."""
    if (
        not enabled()
        or (message is not None and type(message) is not str)
        or (code is not None and type(code) is not str)
    ):
        return None
    try:
        result = _native.diagnostic_message(message, code)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def xml_kind_from_suffix(suffix: Any) -> str | None:
    """Classify supported ASCII XML suffixes natively, or request Python fallback."""
    if not enabled() or type(suffix) is not str or not suffix.isascii():
        return None
    try:
        result = _native.xml_kind_from_suffix(suffix)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def command_short_name(type_name: Any) -> str | None:
    """Shorten supported ASCII command type names natively, or request fallback."""
    if not enabled() or type(type_name) is not str or not type_name.isascii():
        return None
    try:
        result = _native.command_short_name(type_name)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
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


def worklist_parse_bool(value: Any) -> bool | None:
    """Parse supported exact ASCII worklist booleans; invalid forms fall back."""
    if not enabled() or type(value) is not str or not value.isascii():
        return None
    try:
        result = _native.worklist_parse_bool(value)
        return result if type(result) is bool else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
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


def worklist_parse_tip_selection(
    raw: str, supported_channels: int | None
) -> dict[str, Any] | None:
    """Parse common ASCII tip masks natively, or request Python fallback."""
    if (
        not enabled()
        or type(raw) is not str
        or (
            supported_channels is not None
            and (type(supported_channels) is not int or supported_channels < 0)
        )
    ):
        return None
    try:
        encoded = _native.worklist_parse_tip_selection(raw, supported_channels)
        if encoded is None:
            return None
        result = json.loads(encoded)
        if (
            not isinstance(result, dict)
            or not {"raw", "mode", "value", "channel", "status", "diagnostic_code"}.issubset(result)
            or result.get("raw") != raw
            or result.get("mode") not in {"automatic", "explicit"}
            or result.get("status")
            not in {"valid", "malformed", "invalid", "non_one_hot", "out_of_range"}
            or (result.get("value") is not None and type(result["value"]) is not int)
            or (result.get("channel") is not None and type(result["channel"]) is not int)
            or (
                result.get("diagnostic_code") is not None
                and not isinstance(result["diagnostic_code"], str)
            )
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


def worklist_parse_enabled_tip_mask(value: str, supported_channels: int) -> int | None:
    """Parse an in-range command mask natively; invalid cases retain Python errors."""
    if (
        not enabled()
        or type(value) is not str
        or not value.isascii()
        or any(0x1C <= ord(character) <= 0x1F for character in value)
        or type(supported_channels) is not int
        or not 1 <= supported_channels <= 127
    ):
        return None
    try:
        result = _native.worklist_parse_enabled_tip_mask(value, supported_channels)
        if not isinstance(result, str):
            return None
        mask = int(result, 10)
        maximum = (1 << supported_channels) - 1
        return mask if 0 < mask <= maximum else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
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


def worklist_build_schedule(
    transfers: Any, wash_policy: str, batch_size: int | None
) -> list[dict[str, Any]] | None:
    """Return native worklist operations while preserving Python record objects."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_build_schedule(
            _encode_json(transfers), wash_policy, batch_size
        )
        if encoded is None:
            return None
        result = json.loads(encoded)
        kinds = {"comment", "aspirate", "dispense", "wash", "break"}
        if not isinstance(result, list) or not all(
            isinstance(operation, dict)
            and operation.get("kind") in kinds
            and type(operation.get("transfer_index")) is int
            and operation["transfer_index"] >= 0
            for operation in result
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


def _decode_worklist_record_payload(encoded: str | None) -> list[dict[str, Any]] | None:
    if not isinstance(encoded, str):
        return None
    try:
        records = json.loads(encoded)
        if not isinstance(records, list):
            return None
        for record in records:
            if not isinstance(record, dict):
                return None
            kind = record.get("kind")
            if kind == "pipette":
                fields = (
                    "operation",
                    "rack_label",
                    "rack_id",
                    "rack_type",
                    "position",
                    "tube_id",
                    "volume",
                    "liquid_class",
                    "tip_mask",
                    "forced_rack_type",
                )
                if (
                    record.get("operation") not in {"A", "D"}
                    or not all(isinstance(record.get(field), str) for field in fields)
                ):
                    return None
            elif kind == "wash":
                scheme = record.get("scheme")
                if "scheme" not in record or (
                    scheme is not None
                    and (type(scheme) is not int or scheme not in {1, 2, 3, 4})
                ):
                    return None
            elif kind == "break":
                continue
            elif kind == "comment":
                if not isinstance(record.get("text"), str):
                    return None
            elif kind in {"flush", "set_diti_type"}:
                expected_type = "F" if kind == "flush" else "S"
                if (
                    record.get("type_character") != expected_type
                    or not isinstance(record.get("raw_line"), str)
                    or not isinstance(record.get("parameters"), list)
                    or not all(isinstance(item, str) for item in record["parameters"])
                ):
                    return None
            elif kind == "raw":
                if not isinstance(record.get("type_character"), str) or not isinstance(
                    record.get("raw_line"), str
                ):
                    return None
            else:
                return None
        return records
    except (
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_parse_gwl_records(
    lines: Any, permissive: bool
) -> list[dict[str, Any]] | None:
    """Parse supported GWL lines natively, preserving Python record adapters."""
    if not enabled() or type(permissive) is not bool:
        return None
    try:
        encoded = _native.worklist_parse_gwl_records(_encode_json(lines), permissive)
        return _decode_worklist_record_payload(encoded)
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return None


def worklist_inspect_gwl_lines(lines: Any) -> dict[str, Any] | None:
    """Summarize supported ASCII GWL lines natively, or request Python fallback."""
    if (
        not enabled()
        or type(lines) is not list
        or not all(
            type(line) is str
            and line.isascii()
            and not any(0x1C <= ord(character) <= 0x1F for character in line)
            for line in lines
        )
    ):
        return None
    try:
        encoded = _native.worklist_inspect_gwl_lines(_encode_json(lines))
        if not isinstance(encoded, str):
            return None
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            return None
        pairs = payload.get("record_counts")
        line_count = payload.get("line_count")
        transfer_pairs = payload.get("transfer_pairs_estimate")
        raw_examples = payload.get("pipette_examples")
        if (
            not isinstance(pairs, list)
            or type(line_count) is not int
            or line_count != len(lines)
            or type(transfer_pairs) is not int
            or transfer_pairs < 0
            or not isinstance(raw_examples, list)
            or len(raw_examples) > 12
        ):
            return None
        counts: dict[str, int] = {}
        for pair in pairs:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or type(pair[1]) is not int
                or pair[1] < 0
                or pair[0] in counts
            ):
                return None
            counts[pair[0]] = pair[1]
        examples = []
        for example in raw_examples:
            if (
                not isinstance(example, list)
                or len(example) != 7
                or not all(isinstance(value, str) for value in example)
            ):
                return None
            line, operation, rack_label, rack_type, position, volume, liquid_class = example
            examples.append(
                {
                    "line": line,
                    "operation": operation,
                    "rack_label": rack_label,
                    "rack_type": rack_type,
                    "position": position,
                    "volume": volume,
                    "liquid_class": liquid_class,
                }
            )
        return {
            "record_counts": counts,
            "line_count": line_count,
            "transfer_pairs_estimate": transfer_pairs,
            "pipette_examples": examples,
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


def worklist_parse_gwl_text_records(
    text: str, permissive: bool
) -> list[dict[str, Any]] | None:
    """Parse common GWL text natively while retaining line-splitting fallback."""
    if not enabled() or type(text) is not str or type(permissive) is not bool:
        return None
    try:
        encoded = _native.worklist_parse_gwl_text_records(text, permissive)
        return _decode_worklist_record_payload(encoded)
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return None


def worklist_read_gwl_records(
    path: str | Path, permissive: bool
) -> list[dict[str, Any]] | None:
    """Read and parse supported GWL files natively, preserving Python fallback."""
    if not enabled() or type(permissive) is not bool:
        return None
    try:
        encoded = _native.worklist_read_gwl_records(str(path), permissive)
        return _decode_worklist_record_payload(encoded)
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


def worklist_format_volume(value: Any) -> str | None:
    """Format supported exact numeric GWL volumes natively, or request fallback."""
    if not enabled() or type(value) not in (str, int, float):
        return None
    if type(value) is str and (
        not value.isascii()
        or any(0x1C <= ord(character) <= 0x1F for character in value)
    ):
        return None
    try:
        result = _native.worklist_format_volume(value)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, OverflowError, UnicodeError):
        return None


def worklist_comment_line(text: Any) -> str | None:
    """Serialize exact-string GWL comments natively, or request Python fallback."""
    if not enabled() or type(text) is not str:
        return None
    try:
        result = _native.worklist_comment_line(text)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_clean_field(value: Any) -> str | None:
    """Trim supported ASCII GWL fields natively, preserving Python fallback cases."""
    if not enabled() or (
        value is not None
        and (
            type(value) is not str
            or not value.isascii()
            or any(0x1C <= ord(character) <= 0x1F for character in value)
        )
    ):
        return None
    try:
        result = _native.worklist_clean_field(value)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_ascii_uppercase(value: Any) -> str | None:
    """Uppercase exact ASCII strings natively, preserving Unicode fallback."""
    if not enabled() or type(value) is not str or not value.isascii():
        return None
    try:
        result = _native.worklist_ascii_uppercase(value)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_wash_type(scheme: Any) -> str | None:
    """Format exact integer or ``None`` wash schemes natively, or request fallback."""
    if not enabled() or (scheme is not None and type(scheme) is not int):
        return None
    try:
        result = _native.worklist_wash_type(scheme)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def worklist_record_type_line(type_character: Any) -> str | None:
    """Format exact-string GWL record types natively, or request fallback."""
    if not enabled() or type(type_character) is not str:
        return None
    try:
        result = _native.worklist_record_type_line(type_character)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_join_gwl_fields(fields: Any) -> str | None:
    """Join exact-text GWL fields natively, or request Python fallback."""
    if (
        not enabled()
        or type(fields) not in (list, tuple)
        or not all(type(field) is str for field in fields)
    ):
        return None
    try:
        result = _native.worklist_join_gwl_fields(_encode_json(fields))
        return result if isinstance(result, str) else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
    ):
        return None


def worklist_serialize_gwl_lines(lines: Any) -> str | None:
    """Join exact text records natively, or request Python string-join fallback."""
    if (
        not enabled()
        or type(lines) not in (list, tuple)
        or not all(type(line) is str for line in lines)
    ):
        return None
    try:
        result = _native.worklist_serialize_gwl_lines(_encode_json(lines))
        return result if isinstance(result, str) else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_count_record_types(types: Any) -> dict[str, int] | None:
    """Count exact string record types natively, preserving first-seen order."""
    if (
        not enabled()
        or type(types) not in (list, tuple)
        or not all(type(record_type) is str for record_type in types)
    ):
        return None
    try:
        encoded = _native.worklist_count_record_types(_encode_json(types))
        if encoded is None:
            return None
        pairs = json.loads(encoded)
        if not isinstance(pairs, list):
            return None
        counts: dict[str, int] = {}
        for pair in pairs:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or type(pair[1]) is not int
                or pair[1] < 0
                or pair[0] in counts
            ):
                return None
            counts[pair[0]] = pair[1]
        return counts
    except (
        AttributeError,
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
