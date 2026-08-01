"""Compatibility wrapper around :mod:`tecan_common.gwl`."""

from tecan_common.gwl import (  # noqa: F401
    Break,
    Comment,
    Pipette,
    RawRecord,
    Record,
    Wash,
    Worklist,
    parse_gwl,
    parse_gwl_line,
    parse_gwl_lines,
    parse_gwl_text,
    serialize_gwl,
)

__all__ = [
    "Break",
    "Comment",
    "Pipette",
    "RawRecord",
    "Record",
    "Wash",
    "Worklist",
    "parse_gwl",
    "parse_gwl_line",
    "parse_gwl_lines",
    "parse_gwl_text",
    "serialize_gwl",
]
