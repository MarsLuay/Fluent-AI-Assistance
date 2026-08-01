"""Minimal Gemini WorkList record model.

The common aspirate/dispense row shape is:

    A;RackLabel;RackID;RackType;Position;TubeID;Volume;LiquidClass;TipType;TipMask;ForcedRackType
    D;RackLabel;RackID;RackType;Position;TubeID;Volume;LiquidClass;TipType;TipMask;ForcedRackType

The TipType field is reserved in this format and is emitted as empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol


class Record(Protocol):
    type_character: str

    def to_line(self) -> str:
        ...


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


@dataclass(frozen=True)
class RawRecord:
    type_character: str
    raw_line: str

    def to_line(self) -> str:
        return self.raw_line


@dataclass(frozen=True)
class Pipette:
    operation: str
    rack_label: str
    rack_type: str
    position: int | str
    volume: float | int | str
    rack_id: str = ""
    tube_id: str = ""
    liquid_class: str = ""
    tip_mask: str = ""
    forced_rack_type: str = ""
    type_character: str = field(init=False)

    def __post_init__(self) -> None:
        op = _clean(self.operation).upper()
        if not op or op[0] not in {"A", "D"}:
            raise ValueError("Pipette operation must be 'A' or 'D'.")
        object.__setattr__(self, "type_character", op[0])

    def to_line(self) -> str:
        values = [
            self.type_character,
            self.rack_label,
            self.rack_id,
            self.rack_type,
            self.position,
            self.tube_id,
            _format_volume(self.volume),
            self.liquid_class,
            "",
            self.tip_mask,
            self.forced_rack_type,
        ]
        return ";".join(_clean(value) for value in values)


@dataclass(frozen=True)
class Wash:
    scheme: int | None = None
    type_character: str = field(init=False)

    def __post_init__(self) -> None:
        if self.scheme is None:
            object.__setattr__(self, "type_character", "W")
            return
        if self.scheme not in {1, 2, 3, 4}:
            raise ValueError("Wash scheme must be between 1 and 4.")
        object.__setattr__(self, "type_character", f"W{self.scheme}")

    def to_line(self) -> str:
        return f"{self.type_character};"


@dataclass(frozen=True)
class Break:
    type_character: str = "B"

    def to_line(self) -> str:
        return "B;"


@dataclass(frozen=True)
class Comment:
    text: str
    type_character: str = "C"

    def to_line(self) -> str:
        return "C;" + self.text.replace("\n", "\\n")


@dataclass
class Worklist:
    name: str = "worklist"
    records: list[Record] = field(default_factory=list)

    def add(self, record: Record) -> None:
        self.records.append(record)

    def extend(self, records: Iterable[Record]) -> None:
        self.records.extend(records)

    def to_text(self) -> str:
        return serialize_gwl(self.records)

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_text(), encoding="utf-8")

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.type_character] = counts.get(record.type_character, 0) + 1
        return counts


def serialize_gwl(records: Iterable[Record]) -> str:
    lines = [record.to_line() for record in records]
    return "\n".join(lines) + "\n"


def parse_gwl(path: str | Path, *, permissive: bool = False) -> Worklist:
    source = Path(path)
    return parse_gwl_text(source.read_text(encoding="utf-8-sig"), name=source.stem, permissive=permissive)


def parse_gwl_text(text: str, *, name: str = "worklist", permissive: bool = False) -> Worklist:
    return parse_gwl_lines(text.splitlines(), name=name, permissive=permissive)


def parse_gwl_lines(lines: Iterable[str], *, name: str = "worklist", permissive: bool = False) -> Worklist:
    worklist = Worklist(name=name)
    for line_no, raw_line in enumerate(lines, start=1):
        record = parse_gwl_line(raw_line, line_no=line_no, permissive=permissive)
        if record is not None:
            worklist.add(record)
    return worklist


def parse_gwl_line(raw_line: str, *, line_no: int | None = None, permissive: bool = False) -> Record | None:
    if not raw_line.strip():
        return None

    line = raw_line.rstrip("\r\n")
    parts = line.split(";")
    op = parts[0].strip().upper()
    prefix = f"Line {line_no}: " if line_no is not None else ""

    if op in {"A", "D"}:
        if not permissive and len(parts) != 11:
            raise ValueError(f"{prefix}pipette record has {len(parts)} fields, expected 11.")
        normalized = (parts + [""] * 11)[:11]
        return Pipette(
            operation=op,
            rack_label=normalized[1],
            rack_id=normalized[2],
            rack_type=normalized[3],
            position=normalized[4],
            tube_id=normalized[5],
            volume=normalized[6],
            liquid_class=normalized[7],
            tip_mask=normalized[9],
            forced_rack_type=normalized[10],
        )

    if op == "W":
        if not permissive and len(parts) != 2:
            raise ValueError(f"{prefix}wash record has {len(parts)} fields, expected 2.")
        return Wash()
    if op in {"W1", "W2", "W3", "W4"}:
        if not permissive and len(parts) != 2:
            raise ValueError(f"{prefix}wash record has {len(parts)} fields, expected 2.")
        return Wash(scheme=int(op[1]))
    if op == "B":
        if not permissive and len(parts) != 2:
            raise ValueError(f"{prefix}break record has {len(parts)} fields, expected 2.")
        return Break()
    if op == "C":
        if not permissive and len(parts) < 2:
            raise ValueError(f"{prefix}comment record has {len(parts)} fields, expected at least 2.")
        return Comment(";".join(parts[1:]))
    if permissive:
        return RawRecord(type_character=op, raw_line=line)
    raise ValueError(f"{prefix}unsupported record type {op!r}.")


def _format_volume(value: float | int | str) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        try:
            numeric = float(stripped)
        except ValueError:
            return stripped
    else:
        numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.6f}".rstrip("0").rstrip(".")


__all__ = [
    "Break",
    "Comment",
    "Pipette",
    "RawRecord",
    "Record",
    "Worklist",
    "Wash",
    "parse_gwl",
    "parse_gwl_line",
    "parse_gwl_lines",
    "parse_gwl_text",
    "serialize_gwl",
]
