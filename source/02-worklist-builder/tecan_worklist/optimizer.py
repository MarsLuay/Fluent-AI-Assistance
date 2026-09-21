"""Conservative Worklist v2 analysis and opt-in scheduling.

The analyzer is intentionally stricter than a text sorter.  Unknown records,
wash/flush/tip-type state changes, and ``B`` records are execution barriers;
only independent, explicitly one-hot transfers inside one barrier-free window
may be reordered.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from .gwl import (
    Break,
    Comment,
    Flush,
    Pipette,
    RawRecord,
    Record,
    SetDiTiType,
    TipSelection,
    Wash,
    Worklist,
    parse_tip_selection,
)


@dataclass(frozen=True)
class WorklistDiagnostic:
    code: str
    message: str
    line_numbers: tuple[int, ...] = ()
    severity: str = "warning"

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "line_numbers": list(self.line_numbers),
            "severity": self.severity,
        }


@dataclass(frozen=True)
class TransferUnit:
    """A paired A/D operation and the comments that belong to it."""

    unit_index: int
    record_indices: tuple[int, ...]
    line_numbers: tuple[int, ...]
    aspirate: Pipette
    dispense: Pipette
    tip_selection: TipSelection
    source_key: tuple[str, ...]
    destination_key: tuple[str, ...]

    @property
    def tip_channel(self) -> int | None:
        return self.tip_selection.channel

    def signature(self) -> tuple[object, ...]:
        return (
            self.aspirate.rack_label,
            self.aspirate.rack_id,
            self.aspirate.rack_type,
            str(self.aspirate.position),
            self.aspirate.tube_id,
            self.dispense.rack_label,
            self.dispense.rack_id,
            self.dispense.rack_type,
            str(self.dispense.position),
            self.dispense.tube_id,
            str(self.aspirate.volume),
            self.aspirate.liquid_class,
            self.aspirate.tip_mask,
            str(self.dispense.volume),
            self.dispense.liquid_class,
            self.dispense.tip_mask,
            self.aspirate.forced_rack_type,
            self.dispense.forced_rack_type,
        )


@dataclass(frozen=True)
class ParallelWindow:
    index: int
    units: tuple[TransferUnit, ...]
    independent: bool
    explicit_tips: bool
    candidate_channels: tuple[int, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "unit_indices": [unit.unit_index for unit in self.units],
            "independent": self.independent,
            "explicit_tips": self.explicit_tips,
            "candidate_channels": list(self.candidate_channels),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WorklistAnalysis:
    schema_version: str
    record_count: int
    units: tuple[TransferUnit, ...]
    windows: tuple[ParallelWindow, ...]
    barriers: tuple[dict[str, object], ...]
    diagnostics: tuple[WorklistDiagnostic, ...]
    enabled_tip_mask: int | None = None

    @property
    def has_errors(self) -> bool:
        return any(diagnostic.severity == "error" for diagnostic in self.diagnostics)

    def to_dict(self) -> dict[str, object]:
        explicit = [unit.tip_channel for unit in self.units if unit.tip_channel is not None]
        return {
            "schema_version": self.schema_version,
            "record_count": self.record_count,
            "unit_count": len(self.units),
            "explicit_tip_assignments": dict(sorted(Counter(explicit).items())),
            "automatic_tip_units": sum(unit.tip_selection.mode == "automatic" for unit in self.units),
            "enabled_tip_mask": self.enabled_tip_mask,
            "barriers": list(self.barriers),
            "parallel_windows": [window.to_dict() for window in self.windows],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "runtime_parallelism": "FluentControl-dependent; offline grouping is not a timing guarantee.",
        }


@dataclass(frozen=True)
class OptimizationResult:
    worklist: Worklist
    analysis: WorklistAnalysis
    changed: bool
    reordered_units: tuple[tuple[int, ...], ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = self.analysis.to_dict()
        payload.update(
            {
                "changed": self.changed,
                "reordered_units": [list(item) for item in self.reordered_units],
                "reasons": list(self.reasons),
            }
        )
        return payload


def parse_enabled_tip_mask(value: object, *, supported_channels: int = 8) -> int | None:
    """Parse a command-level mask, where multiple bits are allowed."""

    if value in (None, ""):
        return None
    try:
        mask = int(str(value).strip(), 10)
    except (TypeError, ValueError) as exc:
        raise ValueError("worklist_enabled_tip_mask_not_integer") from exc
    max_mask = (1 << supported_channels) - 1
    if mask <= 0 or mask & ~max_mask:
        raise ValueError("worklist_enabled_tip_mask_out_of_range")
    return mask


def analyze_worklist(
    worklist: Worklist | Iterable[Record],
    *,
    enabled_tip_mask: object = None,
    supported_channels: int = 8,
) -> WorklistAnalysis:
    records = list(worklist.records if isinstance(worklist, Worklist) else worklist)
    enabled = parse_enabled_tip_mask(enabled_tip_mask, supported_channels=supported_channels)
    diagnostics: list[WorklistDiagnostic] = []
    barriers: list[dict[str, object]] = []
    units: list[TransferUnit] = []
    windows: list[ParallelWindow] = []
    current: list[TransferUnit] = []
    pending_comments: list[int] = []

    def close_window(reason: str) -> None:
        nonlocal current
        if current:
            independent, explicit, channels, detail = _window_properties(current)
            windows.append(
                ParallelWindow(
                    len(windows),
                    tuple(current),
                    independent,
                    explicit,
                    channels,
                    reason if not independent else detail,
                )
            )
            current = []

    index = 0
    while index < len(records):
        record = records[index]
        if isinstance(record, Comment):
            pending_comments.append(index)
            index += 1
            continue
        if isinstance(record, Pipette) and record.operation == "A":
            if index + 1 >= len(records) or not isinstance(records[index + 1], Pipette) or records[index + 1].operation != "D":
                diagnostics.append(
                    WorklistDiagnostic(
                        "unpaired_aspirate",
                        "A record is not immediately followed by a D record; scheduling stops at this point.",
                        (index + 1,),
                        "error",
                    )
                )
                close_window("unpaired A record")
                barriers.append({"record_index": index, "line_number": index + 1, "type": record.type_character, "reason": "unpaired_aspirate"})
                pending_comments.clear()
                index += 1
                continue
            dispense = records[index + 1]
            assert isinstance(dispense, Pipette)
            selection_a = parse_tip_selection(record.tip_mask, supported_channels=supported_channels)
            selection_d = parse_tip_selection(dispense.tip_mask, supported_channels=supported_channels)
            _append_tip_diagnostic(diagnostics, selection_a, index + 1)
            _append_tip_diagnostic(diagnostics, selection_d, index + 2)
            if selection_a.valid and selection_d.valid and selection_a.value != selection_d.value:
                diagnostics.append(
                    WorklistDiagnostic(
                        "record_tip_selection_mismatch",
                        "The A/D pair changes explicit TipMask selection.",
                        (index + 1, index + 2),
                        "error",
                    )
                )
            selection = selection_a if selection_a.mode == "explicit" else selection_d
            if enabled is not None and selection.value is not None and not (selection.value & enabled):
                diagnostics.append(
                    WorklistDiagnostic(
                        "record_tip_not_enabled",
                        "The record-level selected tip is not enabled by the enclosing Load Worklist mask.",
                        (index + 1, index + 2),
                        "error",
                    )
                )
            elif selection.value is not None and enabled is None:
                diagnostics.append(
                    WorklistDiagnostic(
                        "worklist_tip_selection_unavailable",
                        "The enclosing Load Worklist tip-selection mask was not supplied; subset compatibility is unproven.",
                        (index + 1, index + 2),
                    )
                )
            unit = TransferUnit(
                unit_index=len(units),
                record_indices=tuple((*pending_comments, index, index + 1)),
                line_numbers=tuple(item + 1 for item in (*pending_comments, index, index + 1)),
                aspirate=record,
                dispense=dispense,
                tip_selection=selection,
                source_key=_location_key(record),
                destination_key=_location_key(dispense),
            )
            units.append(unit)
            current.append(unit)
            pending_comments.clear()
            index += 2
            continue
        if isinstance(record, Pipette) and record.operation == "D":
            diagnostics.append(
                WorklistDiagnostic("unpaired_dispense", "A D record is not paired with a preceding A record.", (index + 1,), "error")
            )
        else:
            close_window(f"{record.type_character} state boundary")
            barriers.append(
                {
                    "record_index": index,
                    "line_number": index + 1,
                    "type": record.type_character,
                    "raw_line": record.to_line(),
                    "reason": _barrier_reason(record),
                }
            )
            if isinstance(record, RawRecord):
                diagnostics.append(
                    WorklistDiagnostic(
                        "unknown_record_barrier",
                        f"Unknown record {record.type_character!r} prevents safe reordering.",
                        (index + 1,),
                    )
                )
        pending_comments.clear()
        index += 1
    close_window("end of worklist")
    if pending_comments:
        barriers.append({"record_index": pending_comments[-1], "line_number": pending_comments[-1] + 1, "type": "C", "reason": "trailing_comment"})
    return WorklistAnalysis(
        schema_version="tecan.worklist.semantics.v1",
        record_count=len(records),
        units=tuple(units),
        windows=tuple(windows),
        barriers=tuple(barriers),
        diagnostics=tuple(diagnostics),
        enabled_tip_mask=enabled,
    )


def optimize_worklist(
    worklist: Worklist,
    *,
    enabled_tip_mask: object = None,
    supported_channels: int = 8,
) -> OptimizationResult:
    analysis = analyze_worklist(worklist, enabled_tip_mask=enabled_tip_mask, supported_channels=supported_channels)
    if analysis.has_errors:
        return OptimizationResult(
            Worklist(name=worklist.name, records=list(worklist.records)),
            analysis,
            False,
            reasons=("optimization refused because the worklist has semantic errors",),
        )
    records = list(worklist.records)
    replacements: dict[int, list[Record]] = {}
    reordered: list[tuple[int, ...]] = []
    reasons: list[str] = []
    for window in analysis.windows:
        if len(window.units) < 2:
            continue
        if not window.independent:
            reasons.append(f"window {window.index}: shared source/destination state prevents reordering")
            continue
        if not window.explicit_tips:
            reasons.append(f"window {window.index}: automatic tip selection is not rewritten")
            continue
        grouped: dict[int, list[TransferUnit]] = defaultdict(list)
        for unit in window.units:
            assert unit.tip_channel is not None
            grouped[unit.tip_channel].append(unit)
        channels = sorted(grouped)
        if len(channels) < 2:
            reasons.append(f"window {window.index}: only one explicit tip channel is available")
            continue
        candidate: list[TransferUnit] = []
        offset = 0
        while True:
            added = False
            for channel in channels:
                if offset < len(grouped[channel]):
                    candidate.append(grouped[channel][offset])
                    added = True
            if not added:
                break
            offset += 1
        if [unit.unit_index for unit in candidate] == [unit.unit_index for unit in window.units]:
            reasons.append(f"window {window.index}: order is already tip-interleaved")
            continue
        first = min(min(unit.record_indices) for unit in window.units)
        blocks = {min(unit.record_indices): [records[item] for item in unit.record_indices] for unit in window.units}
        replacements[first] = [record for unit in candidate for record in blocks[min(unit.record_indices)]]
        for unit in window.units:
            for item in unit.record_indices:
                if item != first:
                    records[item] = None  # type: ignore[assignment]
        reordered.append(tuple(unit.unit_index for unit in candidate))
    if replacements:
        output: list[Record] = []
        for index, record in enumerate(records):
            if index in replacements:
                output.extend(replacements[index])
            elif record is not None:
                output.append(record)
        optimized = Worklist(name=worklist.name, records=output)
    else:
        optimized = Worklist(name=worklist.name, records=list(worklist.records))
    return OptimizationResult(optimized, analysis, bool(replacements), tuple(reordered), tuple(reasons))


def semantic_equivalent(
    left: Worklist,
    right: Worklist,
    *,
    enabled_tip_mask: object = None,
    supported_channels: int = 8,
) -> bool:
    """Compare transfer and barrier semantics, ignoring safe unit ordering."""

    left_analysis = analyze_worklist(
        left,
        enabled_tip_mask=enabled_tip_mask,
        supported_channels=supported_channels,
    )
    right_analysis = analyze_worklist(
        right,
        enabled_tip_mask=enabled_tip_mask,
        supported_channels=supported_channels,
    )
    if left_analysis.has_errors or right_analysis.has_errors:
        return False
    left_barriers = [(item["type"], item.get("raw_line")) for item in left_analysis.barriers]
    right_barriers = [(item["type"], item.get("raw_line")) for item in right_analysis.barriers]
    if left_barriers != right_barriers:
        return False
    if len(left_analysis.windows) != len(right_analysis.windows):
        return False
    return all(
        Counter(unit.signature() for unit in left_window.units)
        == Counter(unit.signature() for unit in right_window.units)
        for left_window, right_window in zip(left_analysis.windows, right_analysis.windows)
    )


def _append_tip_diagnostic(diagnostics: list[WorklistDiagnostic], selection: TipSelection, line: int) -> None:
    if selection.valid:
        return
    diagnostics.append(
        WorklistDiagnostic(
            selection.diagnostic_code or "tip_mask_invalid",
            f"Record-level TipMask {selection.raw!r} is not a supported one-hot tip selection.",
            (line,),
            "error",
        )
    )


def _location_key(record: Pipette) -> tuple[str, ...]:
    return (
        record.rack_label,
        record.rack_id,
        record.rack_type,
        str(record.position),
        record.tube_id,
    )


def _window_properties(units: list[TransferUnit]) -> tuple[bool, bool, tuple[int, ...], str]:
    if any(not unit.tip_selection.valid for unit in units):
        return False, False, tuple(sorted({item.tip_channel for item in units if item.tip_channel is not None})), "invalid tip selection prevents reordering"
    seen: set[tuple[str, ...]] = set()
    for unit in units:
        if unit.source_key in seen or unit.destination_key in seen:
            return False, all(item.tip_selection.mode == "explicit" for item in units), tuple(sorted({item.tip_channel for item in units if item.tip_channel is not None})), "shared source/destination state is order-sensitive"
        seen.add(unit.source_key)
        seen.add(unit.destination_key)
    explicit = all(item.tip_selection.mode == "explicit" for item in units)
    channels = tuple(sorted({item.tip_channel for item in units if item.tip_channel is not None}))
    if not explicit:
        return True, False, channels, "independent units may be analyzed, but automatic tips are not rewritten"
    return True, True, channels, "independent explicit-tip units may be interleaved deterministically"


def _barrier_reason(record: Record) -> str:
    if isinstance(record, Break):
        return "explicit execution barrier"
    if isinstance(record, (Wash, Flush, SetDiTiType)):
        return "typed worklist state transition"
    if isinstance(record, Comment):
        return "comment association"
    return "unsupported or state-changing record"


__all__ = [
    "OptimizationResult",
    "ParallelWindow",
    "TransferUnit",
    "WorklistAnalysis",
    "WorklistDiagnostic",
    "analyze_worklist",
    "optimize_worklist",
    "parse_enabled_tip_mask",
    "semantic_equivalent",
]
