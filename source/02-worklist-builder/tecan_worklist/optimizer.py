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

from tecan_common import native_acceleration

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
    if type(value) is str and type(supported_channels) is int:
        native = native_acceleration.worklist_parse_enabled_tip_mask(
            value, supported_channels
        )
        if native is not None:
            return native
    try:
        mask = int(str(value).strip(), 10)
    except (TypeError, ValueError) as exc:
        raise ValueError("worklist_enabled_tip_mask_not_integer") from exc
    max_mask = (1 << supported_channels) - 1
    if mask <= 0 or mask & ~max_mask:
        raise ValueError("worklist_enabled_tip_mask_out_of_range")
    return mask


def _native_worklist_analysis_lines(
    records: list[Record],
    enabled_tip_mask: object,
    enabled_mask: int | None,
    supported_channels: int,
) -> list[str] | None:
    if type(supported_channels) is not int or supported_channels < 0:
        return None
    if enabled_tip_mask is not None and (
        type(enabled_tip_mask) is not str or not enabled_tip_mask.isascii()
    ):
        return None
    if enabled_tip_mask is not None and native_acceleration.worklist_parse_enabled_tip_mask(
        enabled_tip_mask, supported_channels
    ) != enabled_mask:
        return None

    lines: list[str] = []
    for record in records:
        if type(record) is Pipette:
            if record.operation not in {"A", "D"} or type(record.volume) is not int:
                return None
            string_fields = (
                record.rack_label,
                record.rack_id,
                record.rack_type,
                record.tube_id,
                record.liquid_class,
                record.forced_rack_type,
            )
            if any(
                type(value) is not str
                or not value.isascii()
                or value.strip() != value
                or any(character in value for character in ";\r\n")
                for value in string_fields
            ):
                return None
            if type(record.position) is str:
                if (
                    not record.position.isascii()
                    or record.position.strip() != record.position
                    or any(character in record.position for character in ";\r\n")
                ):
                    return None
            elif type(record.position) is not int:
                return None
            if (
                type(record.tip_mask) is not str
                or not record.tip_mask.isascii()
                or any(character in record.tip_mask for character in ";\r\n")
            ):
                return None
            python_selection = parse_tip_selection(
                record.tip_mask, supported_channels=supported_channels
            )
            native_selection = native_acceleration.worklist_parse_tip_selection(
                record.tip_mask, supported_channels
            )
            if native_selection != python_selection.to_dict():
                return None
        elif type(record) is Comment:
            if type(record.text) is not str or not record.text.isascii():
                return None
        elif type(record) is Break:
            pass
        elif type(record) is Wash:
            if record.scheme is not None and (
                type(record.scheme) is not int or record.scheme not in {1, 2, 3, 4}
            ):
                return None
        elif type(record) in {Flush, SetDiTiType}:
            if type(record.parameters) is not tuple or any(
                type(value) is not str
                or not value.isascii()
                or any(character in value for character in "\r\n")
                for value in record.parameters
            ):
                return None
        else:
            return None
        try:
            line = record.to_line()
        except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
            return None
        if type(line) is not str or not line.isascii() or "\n" in line or "\r" in line:
            return None
        lines.append(line)
    return lines


def _worklist_analysis_from_native(
    records: list[Record],
    payload: dict[str, object],
    enabled_mask: int | None,
    supported_channels: int,
) -> WorklistAnalysis | None:
    try:
        if payload.get("record_count") != len(records):
            return None
        unit_record_indices = payload["unit_record_indices"]
        if type(unit_record_indices) is not list:
            return None
        units: list[TransferUnit] = []
        for unit_index, indices in enumerate(unit_record_indices):
            if (
                type(indices) is not list
                or len(indices) < 2
                or any(type(index) is not int for index in indices)
                or indices != list(range(indices[0], indices[-1] + 1))
                or indices[0] < 0
                or indices[-1] >= len(records)
            ):
                return None
            comment_indices, pair_indices = indices[:-2], indices[-2:]
            if any(type(records[index]) is not Comment for index in comment_indices):
                return None
            if pair_indices[1] != pair_indices[0] + 1:
                return None
            aspirate, dispense = (records[index] for index in pair_indices)
            if (
                type(aspirate) is not Pipette
                or type(dispense) is not Pipette
                or aspirate.operation != "A"
                or dispense.operation != "D"
            ):
                return None
            selection_a = parse_tip_selection(
                aspirate.tip_mask, supported_channels=supported_channels
            )
            selection_d = parse_tip_selection(
                dispense.tip_mask, supported_channels=supported_channels
            )
            selection = selection_a if selection_a.mode == "explicit" else selection_d
            units.append(
                TransferUnit(
                    unit_index=unit_index,
                    record_indices=tuple(indices),
                    line_numbers=tuple(index + 1 for index in indices),
                    aspirate=aspirate,
                    dispense=dispense,
                    tip_selection=selection,
                    source_key=_location_key(aspirate),
                    destination_key=_location_key(dispense),
                )
            )
        raw_windows = payload["parallel_windows"]
        if type(raw_windows) is not list:
            return None
        windows: list[ParallelWindow] = []
        for expected_index, item in enumerate(raw_windows):
            if type(item) is not dict:
                return None
            index = item.get("index")
            unit_indices = item.get("unit_indices")
            channels = item.get("candidate_channels")
            independent = item.get("independent")
            explicit = item.get("explicit_tips")
            reason = item.get("reason")
            if (
                type(index) is not int
                or index != expected_index
                or type(unit_indices) is not list
                or any(
                    type(value) is not int or value < 0 or value >= len(units)
                    for value in unit_indices
                )
                or type(channels) is not list
                or any(type(value) is not int for value in channels)
                or type(independent) is not bool
                or type(explicit) is not bool
                or type(reason) is not str
            ):
                return None
            windows.append(
                ParallelWindow(
                    index=index,
                    units=tuple(units[value] for value in unit_indices),
                    independent=independent,
                    explicit_tips=explicit,
                    candidate_channels=tuple(channels),
                    reason=reason,
                )
            )
        raw_barriers = payload["barriers"]
        if type(raw_barriers) is not list or any(type(item) is not dict for item in raw_barriers):
            return None
        raw_diagnostics = payload["diagnostics"]
        if type(raw_diagnostics) is not list:
            return None
        diagnostics: list[WorklistDiagnostic] = []
        for item in raw_diagnostics:
            if type(item) is not dict:
                return None
            code, message = item.get("code"), item.get("message")
            line_numbers, severity = item.get("line_numbers"), item.get("severity")
            if (
                type(code) is not str
                or type(message) is not str
                or type(line_numbers) is not list
                or any(type(line) is not int for line in line_numbers)
                or type(severity) is not str
            ):
                return None
            diagnostics.append(
                WorklistDiagnostic(code, message, tuple(line_numbers), severity)
            )
        schema_version = payload.get("schema_version")
        if (
            type(schema_version) is not str
            or len(units) != payload.get("unit_count")
            or payload.get("enabled_tip_mask") != enabled_mask
        ):
            return None
        analysis = WorklistAnalysis(
            schema_version=schema_version,
            record_count=len(records),
            units=tuple(units),
            windows=tuple(windows),
            barriers=tuple(raw_barriers),
            diagnostics=tuple(diagnostics),
            enabled_tip_mask=enabled_mask,
        )
        analysis_payload = analysis.to_dict()
        native_explicit = payload.get("explicit_tip_assignments")
        if not isinstance(native_explicit, dict) or any(
            type(key) is not str
            or not key.isascii()
            or not key.isdecimal()
            or type(value) is not int
            for key, value in native_explicit.items()
        ):
            return None
        normalized_explicit = {int(key): value for key, value in native_explicit.items()}
        if any(
            analysis_payload.get(key) != payload.get(key)
            for key in (
                "schema_version",
                "record_count",
                "unit_count",
                "automatic_tip_units",
                "enabled_tip_mask",
                "barriers",
                "parallel_windows",
                "diagnostics",
                "runtime_parallelism",
            )
        ) or analysis_payload["explicit_tip_assignments"] != normalized_explicit or (
            payload.get("ok") != (not analysis.has_errors)
        ):
            return None
        return analysis
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def analyze_worklist(
    worklist: Worklist | Iterable[Record],
    *,
    enabled_tip_mask: object = None,
    supported_channels: int = 8,
) -> WorklistAnalysis:
    records = list(worklist.records if isinstance(worklist, Worklist) else worklist)
    enabled = parse_enabled_tip_mask(enabled_tip_mask, supported_channels=supported_channels)
    native_lines = _native_worklist_analysis_lines(
        records, enabled_tip_mask, enabled, supported_channels
    )
    if native_lines is not None:
        native = native_acceleration.worklist_analyze_detail_lines(
            native_lines,
            enabled_tip_mask if type(enabled_tip_mask) is str else None,
            supported_channels,
        )
        if native is not None:
            analysis = _worklist_analysis_from_native(
                records, native, enabled, supported_channels
            )
            if analysis is not None:
                return analysis
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


def _native_optimization_result(
    worklist: Worklist,
    records: list[Record],
    lines: list[str],
    analysis: WorklistAnalysis,
    enabled_tip_mask: object,
    supported_channels: int,
) -> OptimizationResult | None:
    payload = native_acceleration.worklist_optimize_lines(
        lines,
        worklist.name,
        enabled_tip_mask if type(enabled_tip_mask) is str else None,
        supported_channels,
    )
    if payload is None or payload.get("ok") is not True:
        return None

    expected_payload = analysis.to_dict()
    native_explicit = payload.get("explicit_tip_assignments")
    if not isinstance(native_explicit, dict) or any(
        type(key) is not str
        or not key.isascii()
        or not key.isdecimal()
        or type(value) is not int
        for key, value in native_explicit.items()
    ):
        return None
    normalized_explicit = {int(key): value for key, value in native_explicit.items()}
    if normalized_explicit != expected_payload["explicit_tip_assignments"] or any(
        payload.get(key) != expected_payload.get(key)
        for key in (
            "schema_version",
            "record_count",
            "unit_count",
            "automatic_tip_units",
            "enabled_tip_mask",
            "barriers",
            "parallel_windows",
            "diagnostics",
            "runtime_parallelism",
        )
    ):
        return None

    raw_orders = payload.get("reordered_units")
    raw_reasons = payload.get("reasons")
    if (
        type(raw_orders) is not list
        or any(
            type(order) is not list or any(type(index) is not int for index in order)
            for order in raw_orders
        )
        or type(raw_reasons) is not list
        or any(type(reason) is not str for reason in raw_reasons)
    ):
        return None

    units_by_index = {unit.unit_index: unit for unit in analysis.units}
    windows_by_unit = {
        unit.unit_index: window for window in analysis.windows for unit in window.units
    }
    replacements: dict[int, list[Record]] = {}
    removed: set[int] = set()
    reordered: list[tuple[int, ...]] = []
    previous_window = -1
    for raw_order in raw_orders:
        order = tuple(raw_order)
        if len(order) < 2 or len(set(order)) != len(order):
            return None
        window = windows_by_unit.get(order[0])
        if (
            window is None
            or window.index <= previous_window
            or any(windows_by_unit.get(index) is not window for index in order)
            or not window.independent
            or not window.explicit_tips
        ):
            return None
        previous_window = window.index
        original_order = [unit.unit_index for unit in window.units]
        if len(order) != len(original_order) or set(order) != set(original_order):
            return None

        grouped: dict[int, list[int]] = defaultdict(list)
        for unit in window.units:
            if unit.tip_channel is None:
                return None
            grouped[unit.tip_channel].append(unit.unit_index)
        channels = sorted(grouped)
        if len(channels) < 2:
            return None
        expected_order: list[int] = []
        offset = 0
        while True:
            added = False
            for channel in channels:
                if offset < len(grouped[channel]):
                    expected_order.append(grouped[channel][offset])
                    added = True
            if not added:
                break
            offset += 1
        if order != tuple(expected_order) or order == tuple(original_order):
            return None

        group_indices: set[int] = set()
        for unit_index in original_order:
            unit = units_by_index.get(unit_index)
            if unit is None:
                return None
            for record_index in unit.record_indices:
                if (
                    type(record_index) is not int
                    or record_index < 0
                    or record_index >= len(records)
                    or record_index in group_indices
                    or record_index in removed
                ):
                    return None
                group_indices.add(record_index)
        first = min(group_indices)
        blocks = {
            unit_index: [records[index] for index in units_by_index[unit_index].record_indices]
            for unit_index in original_order
        }
        replacements[first] = [record for unit_index in order for record in blocks[unit_index]]
        removed.update(index for index in group_indices if index != first)
        reordered.append(order)

    changed = bool(replacements)
    if payload.get("changed") is not changed:
        return None
    output: list[Record] = []
    for index, record in enumerate(records):
        if index in replacements:
            output.extend(replacements[index])
        elif index not in removed:
            output.append(record)
    optimized = Worklist(name=worklist.name, records=output)
    if optimized.to_text() != payload.get("text"):
        return None
    if Counter(record.to_line() for record in optimized.records) != Counter(lines):
        return None
    return OptimizationResult(
        optimized,
        analysis,
        changed,
        tuple(reordered),
        tuple(raw_reasons),
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
    native_lines = _native_worklist_analysis_lines(
        records, enabled_tip_mask, analysis.enabled_tip_mask, supported_channels
    )
    if native_lines is not None:
        native_result = _native_optimization_result(
            worklist,
            records,
            native_lines,
            analysis,
            enabled_tip_mask,
            supported_channels,
        )
        if native_result is not None:
            return native_result
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

    enabled = parse_enabled_tip_mask(
        enabled_tip_mask, supported_channels=supported_channels
    )
    left_lines = _native_worklist_analysis_lines(
        list(left.records), enabled_tip_mask, enabled, supported_channels
    )
    right_lines = _native_worklist_analysis_lines(
        list(right.records), enabled_tip_mask, enabled, supported_channels
    )
    if left_lines is not None and right_lines is not None:
        native = native_acceleration.worklist_semantic_equivalent_lines(
            left_lines,
            right_lines,
            enabled_tip_mask if type(enabled_tip_mask) is str else None,
            supported_channels,
        )
        if native is not None:
            return native

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
    code = selection.diagnostic_code or "tip_mask_invalid"
    native = native_acceleration.worklist_tip_diagnostic(selection.raw, code, line)
    if native is not None:
        diagnostics.append(WorklistDiagnostic(*native))
        return
    diagnostics.append(
        WorklistDiagnostic(
            code,
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
    if type(units) is list and all(
        type(unit) is TransferUnit
        and type(unit.source_key) is tuple
        and all(type(part) is str for part in unit.source_key)
        and type(unit.destination_key) is tuple
        and all(type(part) is str for part in unit.destination_key)
        and type(unit.tip_selection) is TipSelection
        and type(unit.tip_selection.valid) is bool
        and type(unit.tip_selection.mode) is str
        and (
            unit.tip_selection.channel is None
            or type(unit.tip_selection.channel) is int
            and unit.tip_selection.channel >= 0
        )
        for unit in units
    ):
        native = native_acceleration.worklist_window_properties(
            [list(unit.source_key) for unit in units],
            [list(unit.destination_key) for unit in units],
            [unit.tip_selection.valid for unit in units],
            [unit.tip_selection.mode for unit in units],
            [unit.tip_selection.channel for unit in units],
        )
        if native is not None:
            return native
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
        kind = "break"
    elif isinstance(record, (Wash, Flush, SetDiTiType)):
        kind = "state"
    elif isinstance(record, Comment):
        kind = "comment"
    else:
        kind = "other"
    native = native_acceleration.worklist_barrier_reason(kind)
    if native is not None:
        return native
    return {
        "break": "explicit execution barrier",
        "state": "typed worklist state transition",
        "comment": "comment association",
        "other": "unsupported or state-changing record",
    }[kind]


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
