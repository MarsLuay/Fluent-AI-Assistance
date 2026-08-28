"""Transfer CSV loading, validation, and GWL generation."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from .gwl import Break, Comment, Pipette, Wash, Worklist

WashPolicy = Literal["each", "none"]

REQUIRED_COLUMNS = {
    "source_label",
    "source_type",
    "source_position",
    "dest_label",
    "dest_type",
    "dest_position",
    "volume_ul",
}

OPTIONAL_COLUMNS = {
    "liquid_class",
    "source_id",
    "dest_id",
    "source_tube_id",
    "dest_tube_id",
    "tip_mask",
    "forced_source_type",
    "forced_dest_type",
    "comment",
    "wash_after",
    "break_after",
}

_WELL_RE = re.compile(r"^([A-Za-z]+)([1-9][0-9]*)$")


@dataclass(frozen=True)
class Transfer:
    source_label: str
    source_type: str
    source_position: int
    dest_label: str
    dest_type: str
    dest_position: int
    volume_ul: float
    liquid_class: str = ""
    source_id: str = ""
    dest_id: str = ""
    source_tube_id: str = ""
    dest_tube_id: str = ""
    tip_mask: str = ""
    forced_source_type: str = ""
    forced_dest_type: str = ""
    comment: str = ""
    wash_after: bool | None = None
    break_after: bool = False
    row_number: int = 0


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_transfers(path: str | Path, *, well_rows: int = 8) -> list[Transfer]:
    source = Path(path)
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV is empty or missing a header row.")
        fieldnames = {name.strip() for name in reader.fieldnames}
        missing = sorted(REQUIRED_COLUMNS - fieldnames)
        if missing:
            raise ValueError("CSV missing required columns: " + ", ".join(missing))
        transfers = []
        for row_number, row in enumerate(reader, start=2):
            transfers.append(
                _row_to_transfer(row, row_number=row_number, well_rows=well_rows)
            )
    return transfers


def build_worklist(
    transfers: Iterable[Transfer],
    *,
    name: str = "worklist",
    wash_policy: WashPolicy = "each",
    batch_size: int | None = None,
) -> Worklist:
    if wash_policy not in {"each", "none"}:
        raise ValueError("wash_policy must be 'each' or 'none'.")
    if batch_size is not None and batch_size < 1:
        raise ValueError("batch_size must be positive.")

    worklist = Worklist(name=name)
    transfer_count = 0
    for transfer in transfers:
        if transfer.comment:
            worklist.add(Comment(transfer.comment))
        worklist.add(
            Pipette(
                operation="A",
                rack_label=transfer.source_label,
                rack_id=transfer.source_id,
                rack_type=transfer.source_type,
                position=transfer.source_position,
                tube_id=transfer.source_tube_id,
                volume=transfer.volume_ul,
                liquid_class=transfer.liquid_class,
                tip_mask=transfer.tip_mask,
                forced_rack_type=transfer.forced_source_type,
            )
        )
        worklist.add(
            Pipette(
                operation="D",
                rack_label=transfer.dest_label,
                rack_id=transfer.dest_id,
                rack_type=transfer.dest_type,
                position=transfer.dest_position,
                tube_id=transfer.dest_tube_id,
                volume=transfer.volume_ul,
                liquid_class=transfer.liquid_class,
                tip_mask=transfer.tip_mask,
                forced_rack_type=transfer.forced_dest_type,
            )
        )
        transfer_count += 1

        should_wash = transfer.wash_after
        if should_wash is None:
            should_wash = wash_policy == "each"
        if should_wash:
            worklist.add(Wash())
        if transfer.break_after or (
            batch_size is not None and transfer_count % batch_size == 0
        ):
            worklist.add(Break())
    return worklist


def validate_transfers(
    transfers: Iterable[Transfer], *, strict: bool = False
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    seen = list(transfers)
    if not seen:
        errors.append("No transfers found.")

    for transfer in seen:
        prefix = f"CSV row {transfer.row_number}" if transfer.row_number else "Transfer"
        for attr in ("source_label", "source_type", "dest_label", "dest_type"):
            if not getattr(transfer, attr):
                errors.append(f"{prefix}: {attr} is required.")
        if transfer.volume_ul <= 0:
            errors.append(f"{prefix}: volume_ul must be greater than zero.")
        for attr in ("source_position", "dest_position"):
            if getattr(transfer, attr) < 1:
                errors.append(f"{prefix}: {attr} must be >= 1.")
        for attr in (
            "source_label",
            "source_type",
            "dest_label",
            "dest_type",
            "liquid_class",
        ):
            value = getattr(transfer, attr)
            if value and len(value) > 32:
                warnings.append(
                    f"{prefix}: {attr} is longer than 32 characters: {value!r}."
                )
        if transfer.volume_ul > 1000:
            warnings.append(
                f"{prefix}: volume_ul is high for a single transfer: {transfer.volume_ul:g}."
            )

    if strict and warnings:
        errors.extend(f"Strict warning: {warning}" for warning in warnings)
    return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def well_to_position(value: str, *, rows: int = 8) -> int:
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped)
    match = _WELL_RE.match(stripped)
    if not match:
        raise ValueError(f"Invalid well/position value: {value!r}")
    row_name, column_text = match.groups()
    row_index = _row_name_to_index(row_name)
    inferred_rows = max(rows, row_index + 1)
    column = int(column_text)
    return (column - 1) * inferred_rows + row_index + 1


def _row_to_transfer(
    row: dict[str, str], *, row_number: int, well_rows: int
) -> Transfer:
    def get(name: str) -> str:
        value = row.get(name, "")
        return "" if value is None else value.strip()

    try:
        source_position = well_to_position(get("source_position"), rows=well_rows)
        dest_position = well_to_position(get("dest_position"), rows=well_rows)
    except ValueError as exc:
        raise ValueError(f"CSV row {row_number}: {exc}") from exc

    try:
        volume_ul = float(get("volume_ul"))
    except ValueError as exc:
        raise ValueError(f"CSV row {row_number}: volume_ul must be numeric.") from exc

    return Transfer(
        source_label=get("source_label"),
        source_type=get("source_type"),
        source_position=source_position,
        dest_label=get("dest_label"),
        dest_type=get("dest_type"),
        dest_position=dest_position,
        volume_ul=volume_ul,
        liquid_class=get("liquid_class"),
        source_id=get("source_id"),
        dest_id=get("dest_id"),
        source_tube_id=get("source_tube_id"),
        dest_tube_id=get("dest_tube_id"),
        tip_mask=get("tip_mask"),
        forced_source_type=get("forced_source_type"),
        forced_dest_type=get("forced_dest_type"),
        comment=get("comment"),
        wash_after=_parse_optional_bool(get("wash_after")),
        break_after=_parse_bool(get("break_after")),
        row_number=row_number,
    )


def _parse_optional_bool(value: str) -> bool | None:
    if not value:
        return None
    return _parse_bool(value)


def _parse_bool(value: str) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _row_name_to_index(row_name: str) -> int:
    index = 0
    for char in row_name.upper():
        if not ("A" <= char <= "Z"):
            raise ValueError(f"Invalid row name: {row_name!r}")
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1
