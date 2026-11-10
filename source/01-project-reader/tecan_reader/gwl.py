"""Reader for `.gwl` worklists."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from tecan_common.gwl import Pipette, parse_gwl_line, parse_gwl_lines


def inspect_gwl(path: str | Path, *, source_name: str | None = None) -> dict[str, Any]:
    p = Path(path)
    lines = p.read_text(encoding="utf-8-sig").splitlines()
    return inspect_gwl_lines(lines, source_name=source_name or str(path))


def inspect_gwl_lines(lines: list[str], *, source_name: str) -> dict[str, Any]:
    records_with_lines = None
    if type(lines) is list and all(
        type(line) is str
        and line.isascii()
        and not any(0x1C <= ord(character) <= 0x1F for character in line)
        for line in lines
    ):
        records = parse_gwl_lines(lines, permissive=True).records
        nonblank_lines = [
            (line_no, line)
            for line_no, line in enumerate(lines, start=1)
            if line.strip()
        ]
        if len(records) == len(nonblank_lines):
            records_with_lines = (
                (line_no, line, record)
                for (line_no, line), record in zip(nonblank_lines, records)
            )

    if records_with_lines is None:
        records_with_lines = (
            (line_no, line, parse_gwl_line(line, line_no=line_no, permissive=True))
            for line_no, line in enumerate(lines, start=1)
        )

    counts: Counter[str] = Counter()
    pipette_examples: list[dict[str, str]] = []
    for line_no, _line, record in records_with_lines:
        if record is None:
            continue
        counts[record.type_character] += 1
        if isinstance(record, Pipette) and len(pipette_examples) < 12:
            pipette_examples.append(
                {
                    "line": str(line_no),
                    "operation": record.operation,
                    "rack_label": str(record.rack_label),
                    "rack_type": str(record.rack_type),
                    "position": str(record.position),
                    "volume": str(record.volume),
                    "liquid_class": str(record.liquid_class),
                }
            )
    return {
        "kind": "gwl",
        "source": source_name,
        "record_counts": dict(counts),
        "line_count": len(lines),
        "transfer_pairs_estimate": min(counts.get("A", 0), counts.get("D", 0)),
        "pipette_examples": pipette_examples,
    }
