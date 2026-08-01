"""Robotools-style liquid state checks for protocol IR.

This module validates liquid logic before Python, GWL, or XSCR generation.  It
is intentionally conservative: proven overdraws and overfills fail, while
unknown starting state is reported as needs_user so Codex can ask for or infer
the missing liquid inventory before final generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .protocol_ir_schema import Operation, VOLUME_OPERATIONS


LIQUID_STATE_VALIDATION_VERSION = "tecan.liquid_state.v1"


@dataclass
class WellState:
    volume_ul: float | None = None
    reagent: str = ""
    known: bool = False


def validate_liquid_state(ir: dict[str, Any]) -> dict[str, Any]:
    """Validate liquid volumes, well capacity, dead volume, and tip carryover."""
    labware_by_label = _labware_by_label(ir)
    labware_specs = {label: _labware_spec(item) for label, item in labware_by_label.items()}
    wells = _initial_well_states(labware_by_label, labware_specs)
    active_tip = {
        "picked": False,
        "volume_ul": 0.0,
        "known": True,
        "capacity_ul": None,
        "source_labware": "",
    }
    assumptions = [
        {
            "id": "default_tip_carryover_zero",
            "text": "Tip carryover is assumed to be 0 uL unless a step declares tip_carryover_ul.",
        }
    ]
    failures: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    transfers: list[dict[str, Any]] = []

    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        if _is_tip_pickup(operation):
            target = _step_labware(step)
            active_tip = {
                "picked": True,
                "volume_ul": 0.0,
                "known": True,
                "capacity_ul": _tip_capacity_ul(labware_by_label.get(target, {}), step),
                "source_labware": target,
            }
            if active_tip["capacity_ul"] is None:
                warnings.append(_step_issue(step, "unknown_tip_capacity", "Picked tips have unknown capacity."))
            continue
        if _is_tip_release(operation):
            active_tip = {
                "picked": False,
                "volume_ul": 0.0,
                "known": True,
                "capacity_ul": None,
                "source_labware": "",
            }
            continue
        if operation not in VOLUME_OPERATIONS:
            continue

        volume = _resolve_volume_ul(step.get("volume_ul"), ir)
        target = _step_labware(step)
        selected_wells, selection_issue = _step_wells(step, labware_specs.get(target, {}), operation)
        if volume is None or volume <= 0:
            failures.append(_step_issue(step, "invalid_transfer_volume", "Liquid handling volume must be a positive number."))
            continue
        if not target:
            failures.append(_step_issue(step, "missing_labware", "Liquid handling step has no labware target."))
            continue
        if selection_issue:
            unresolved.append(_step_issue(step, "unresolved_well_selection", selection_issue))
            continue
        if not selected_wells:
            unresolved.append(_step_issue(step, "no_wells_selected", "No wells could be selected for this liquid step."))
            continue

        if not active_tip["picked"]:
            warnings.append(
                _step_issue(
                    step,
                    "tip_state_inferred",
                    "No explicit pick_up_tips step is active; tip state is inferred for liquid-state validation.",
                )
            )
            active_tip["picked"] = True
            active_tip["known"] = _is_aspirate(operation)

        if active_tip["capacity_ul"] is not None and volume > float(active_tip["capacity_ul"]):
            failures.append(
                _step_issue(
                    step,
                    "volume_exceeds_tip_capacity",
                    f"{volume:g} uL exceeds active tip capacity {float(active_tip['capacity_ul']):g} uL.",
                    {"tip_capacity_ul": active_tip["capacity_ul"]},
                )
            )

        if _is_aspirate(operation):
            _apply_aspirate(
                step,
                target,
                selected_wells,
                volume,
                labware_specs,
                wells,
                active_tip,
                transfers,
                failures,
                unresolved,
            )
        elif _is_dispense(operation):
            _apply_dispense(
                step,
                target,
                selected_wells,
                volume,
                labware_by_label,
                labware_specs,
                wells,
                active_tip,
                transfers,
                failures,
                unresolved,
                assumptions,
            )
        elif _is_mix(operation):
            _apply_mix(
                step,
                target,
                selected_wells,
                volume,
                labware_specs,
                wells,
                active_tip,
                transfers,
                failures,
                unresolved,
            )

    status = "failed" if failures else ("needs_user" if unresolved else "passed")
    return {
        "validation_version": LIQUID_STATE_VALIDATION_VERSION,
        "status": status,
        "summary": _summary(status, failures, unresolved, warnings, transfers),
        "checked_step_count": _checked_step_count(ir),
        "transfer_count": len(transfers),
        "failure_count": len(failures),
        "unresolved_count": len(unresolved),
        "warning_count": len(warnings),
        "assumptions": assumptions,
        "failures": failures,
        "unresolved": unresolved,
        "warnings": warnings,
        "transfers": transfers[:200],
        "labware": _labware_summary(labware_specs, wells),
    }


def render_liquid_state_markdown(report: dict[str, Any]) -> str:
    """Render a compact liquid-state validation report."""
    lines = [
        "# Liquid State Validation",
        "",
        f"- Result: `{report.get('status')}`",
        f"- Summary: {report.get('summary')}",
        f"- Liquid steps checked: `{report.get('checked_step_count', 0)}`",
        f"- Transfer records: `{report.get('transfer_count', 0)}`",
        "",
        "## Checks",
        "",
        "- Source wells must have enough known liquid above dead volume before aspirate or mix.",
        "- Destination wells must stay at or below max well volume after dispense.",
        "- Tip volume and optional carryover are tracked across aspirate, dispense, mix, and tip changes.",
        "- Missing source state is reported as needs_user instead of assumed safe.",
        "",
    ]
    for title, key in (
        ("Failures", "failures"),
        ("Needs User", "unresolved"),
        ("Warnings", "warnings"),
        ("Assumptions", "assumptions"),
    ):
        items = report.get(key) or []
        lines.extend([f"## {title}", ""])
        if not items:
            lines.append("- none")
        else:
            for item in items[:50]:
                message = item.get("message") or item.get("text") or item.get("reason") or item.get("id")
                prefix = f"step {item.get('index')}: " if item.get("index") else ""
                lines.append(f"- {prefix}{message}")
        lines.append("")

    labware = report.get("labware") or {}
    lines.extend(["## Final Labware State", ""])
    if not labware:
        lines.append("- none")
    else:
        for label, state in labware.items():
            lines.append(
                f"- `{label}`: known wells `{state.get('known_well_count', 0)}`, "
                f"unknown wells `{state.get('unknown_well_count', 0)}`, "
                f"final min/max `{state.get('final_min_volume_ul')}`/`{state.get('final_max_volume_ul')}` uL"
            )
    return "\n".join(lines).rstrip() + "\n"


def liquid_state_failure_message(report: dict[str, Any]) -> str:
    if report.get("status") == "passed":
        return "liquid state validation passed"
    items = list(report.get("failures") or report.get("unresolved") or [])
    if not items:
        return str(report.get("summary") or "liquid state validation did not pass")
    fragments = []
    for item in items[:4]:
        prefix = f"step {item.get('index')}: " if item.get("index") else ""
        fragments.append(prefix + str(item.get("message") or item.get("reason") or "issue"))
    extra = len(items) - len(fragments)
    if extra > 0:
        fragments.append(f"{extra} more issue(s)")
    return "; ".join(fragments)


def _apply_aspirate(
    step: dict[str, Any],
    labware: str,
    selected_wells: list[str],
    volume: float,
    labware_specs: dict[str, dict[str, Any]],
    wells: dict[tuple[str, str], WellState],
    active_tip: dict[str, Any],
    transfers: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> None:
    spec = labware_specs.get(labware, {})
    dead_volume = float(spec.get("dead_volume_ul") or 0)
    for well in selected_wells:
        state = wells.setdefault((labware, well), WellState())
        before = state.volume_ul if state.known else None
        after = before - volume if before is not None else None
        if before is None:
            unresolved.append(
                _step_issue(
                    step,
                    "source_volume_unknown",
                    f"{labware} {well} has unknown starting volume before aspirating {volume:g} uL.",
                    {"labware": labware, "well": well, "transfer_volume_ul": volume},
                )
            )
        elif after < dead_volume:
            failures.append(
                _step_issue(
                    step,
                    "source_below_dead_volume",
                    f"{labware} {well} would end at {after:g} uL, below dead volume {dead_volume:g} uL.",
                    {"labware": labware, "well": well, "before_ul": before, "after_ul": after, "dead_volume_ul": dead_volume},
                )
            )
        else:
            state.volume_ul = after
            state.known = True
        if active_tip.get("known"):
            active_tip["volume_ul"] = float(active_tip.get("volume_ul") or 0) + volume
        transfers.append(_transfer_record(step, "aspirate", labware, well, volume, before, after, active_tip))


def _apply_dispense(
    step: dict[str, Any],
    labware: str,
    selected_wells: list[str],
    volume: float,
    labware_by_label: dict[str, dict[str, Any]],
    labware_specs: dict[str, dict[str, Any]],
    wells: dict[tuple[str, str], WellState],
    active_tip: dict[str, Any],
    transfers: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    assumptions: list[dict[str, Any]],
) -> None:
    spec = labware_specs.get(labware, {})
    max_volume = float(spec.get("max_volume_ul") or 200)
    carryover = _number(_parameter(step, "tip_carryover_ul")) or 0.0
    for well in selected_wells:
        state = wells.setdefault((labware, well), WellState())
        before = state.volume_ul if state.known else None
        if before is None and _is_destination_labware(labware_by_label.get(labware, {})):
            before = 0.0
            state.known = True
            assumptions.append(
                {
                    "id": "destination_assumed_empty",
                    "text": f"{labware} {well} is treated as empty because destination labware has no initial_contents.",
                    "labware": labware,
                    "well": well,
                }
            )
        after = before + volume if before is not None else None
        if before is None:
            if volume > max_volume:
                failures.append(
                    _step_issue(
                        step,
                        "dispense_exceeds_empty_well_capacity",
                        f"{volume:g} uL exceeds max well volume {max_volume:g} uL even if {labware} {well} starts empty.",
                        {"labware": labware, "well": well, "transfer_volume_ul": volume, "max_volume_ul": max_volume},
                    )
                )
            else:
                unresolved.append(
                    _step_issue(
                        step,
                        "destination_volume_unknown",
                        f"{labware} {well} has unknown starting volume before dispensing {volume:g} uL.",
                        {"labware": labware, "well": well, "transfer_volume_ul": volume, "max_volume_ul": max_volume},
                    )
                )
        elif after > max_volume:
            failures.append(
                _step_issue(
                    step,
                    "destination_exceeds_max_volume",
                    f"{labware} {well} would end at {after:g} uL, above max volume {max_volume:g} uL.",
                    {"labware": labware, "well": well, "before_ul": before, "after_ul": after, "max_volume_ul": max_volume},
                )
            )
        else:
            state.volume_ul = after
            state.known = True

        if active_tip.get("known"):
            tip_before = float(active_tip.get("volume_ul") or 0)
            if tip_before + 1e-9 < volume:
                failures.append(
                    _step_issue(
                        step,
                        "dispense_exceeds_tip_volume",
                        f"Dispense volume {volume:g} uL exceeds tracked tip volume {tip_before:g} uL.",
                        {"tip_before_ul": tip_before, "transfer_volume_ul": volume},
                    )
                )
            active_tip["volume_ul"] = max(carryover, max(0.0, tip_before - volume))
        else:
            unresolved.append(
                _step_issue(
                    step,
                    "tip_volume_unknown",
                    "Tip volume before dispense is unknown because no matching aspirate is represented in the IR.",
                    {"transfer_volume_ul": volume},
                )
            )
        transfers.append(_transfer_record(step, "dispense", labware, well, volume, before, after, active_tip))


def _apply_mix(
    step: dict[str, Any],
    labware: str,
    selected_wells: list[str],
    volume: float,
    labware_specs: dict[str, dict[str, Any]],
    wells: dict[tuple[str, str], WellState],
    active_tip: dict[str, Any],
    transfers: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> None:
    spec = labware_specs.get(labware, {})
    dead_volume = float(spec.get("dead_volume_ul") or 0)
    max_volume = float(spec.get("max_volume_ul") or 200)
    for well in selected_wells:
        state = wells.setdefault((labware, well), WellState())
        before = state.volume_ul if state.known else None
        if before is None:
            unresolved.append(
                _step_issue(
                    step,
                    "mix_volume_unknown",
                    f"{labware} {well} has unknown volume before mixing {volume:g} uL.",
                    {"labware": labware, "well": well, "mix_volume_ul": volume},
                )
            )
        elif volume > max_volume or before - volume < dead_volume:
            failures.append(
                _step_issue(
                    step,
                    "mix_volume_not_supported",
                    f"Mix volume {volume:g} uL is not supported by {labware} {well} volume {before:g} uL.",
                    {"labware": labware, "well": well, "before_ul": before, "mix_volume_ul": volume, "dead_volume_ul": dead_volume},
                )
            )
        transfers.append(_transfer_record(step, "mix", labware, well, volume, before, before, active_tip))


def _initial_well_states(
    labware_by_label: dict[str, dict[str, Any]],
    labware_specs: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], WellState]:
    wells: dict[tuple[str, str], WellState] = {}
    for label, item in labware_by_label.items():
        spec = labware_specs.get(label, {})
        for well in _all_wells(spec):
            wells[(label, well)] = WellState()
        for well, volume, reagent in _initial_contents(item, spec):
            wells[(label, well)] = WellState(volume_ul=volume, reagent=reagent, known=True)
    return wells


def _initial_contents(item: dict[str, Any], spec: dict[str, Any]) -> list[tuple[str, float, str]]:
    content = item.get("initial_contents") or {}
    if not isinstance(content, (dict, list)):
        return []
    rows: list[tuple[str, float, str]] = []
    if isinstance(content, list):
        for entry in content:
            if isinstance(entry, dict):
                rows.extend(_initial_contents({"initial_contents": entry}, spec))
        return rows

    reagent = str(content.get("reagent") or content.get("name") or "")
    volume = _number(content.get("volume_ul") or item.get("initial_volume_ul"))
    well_map = content.get("wells")
    if isinstance(well_map, dict):
        for well, value in well_map.items():
            if isinstance(value, dict):
                well_volume = _number(value.get("volume_ul"))
                well_reagent = str(value.get("reagent") or reagent)
            else:
                well_volume = _number(value)
                well_reagent = reagent
            if well_volume is not None:
                rows.append((str(well), well_volume, well_reagent))
        return rows
    if volume is None:
        return rows
    scope = str(content.get("scope") or "").lower()
    selected = _expand_well_selection(content.get("well") or content.get("wells"), spec)
    if scope == "all_wells" or not selected:
        selected = _all_wells(spec)
    for well in selected:
        rows.append((well, volume, reagent))
    return rows


def _step_wells(step: dict[str, Any], spec: dict[str, Any], operation: str) -> tuple[list[str], str]:
    preferred = (
        ("source_wells", "source_well") if _is_aspirate(operation) else
        ("destination_wells", "destination_well") if _is_dispense(operation) else
        ("wells", "well")
    )
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    values = []
    for key in (*preferred, "wells", "well", "well_range", "well_ranges", "selected_wells", "SelectedWellsString"):
        if step.get(key) not in (None, "", []):
            values.append(step.get(key))
        if params.get(key) not in (None, "", []):
            values.append(params.get(key))
    if not values:
        return _all_wells(spec), ""
    wells: list[str] = []
    for value in values:
        expanded = _expand_well_selection(value, spec)
        if expanded:
            wells.extend(expanded)
        elif value not in (None, "", []):
            return [], f"Could not parse well selection {value!r}."
    return _dedupe(wells), ""


def _expand_well_selection(value: Any, spec: dict[str, Any]) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_expand_well_selection(item, spec))
        return _dedupe(out)
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_expand_well_selection(item, spec))
        return _dedupe(out)
    text = str(value).strip()
    lowered = text.lower()
    if lowered in {"all", "all wells", "all_wells"}:
        return _all_wells(spec)
    column_match = re.fullmatch(r"columns?\s+(\d+)(?:\s*[-:]\s*(\d+))?", lowered)
    if column_match:
        start = int(column_match.group(1))
        end = int(column_match.group(2) or start)
        return [f"{_row_name(row)}{column}" for column in range(start, end + 1) for row in range(1, int(spec.get("rows") or 8) + 1)]
    out: list[str] = []
    for token in re.split(r"[,;\s]+", text):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            left, right = token.split(":", 1)
            out.extend(_expand_range(left, right))
        elif _parse_well(token):
            out.append(token.upper())
    return _dedupe(out)


def _expand_range(left: str, right: str) -> list[str]:
    start = _parse_well(left)
    end = _parse_well(right)
    if start is None or end is None:
        return []
    start_row, start_col = start
    end_row, end_col = end
    rows = range(min(start_row, end_row), max(start_row, end_row) + 1)
    cols = range(min(start_col, end_col), max(start_col, end_col) + 1)
    return [f"{_row_name(row)}{col}" for row in rows for col in cols]


def _all_wells(spec: dict[str, Any]) -> list[str]:
    rows = int(spec.get("rows") or 8)
    columns = int(spec.get("columns") or 12)
    return [f"{_row_name(row)}{column}" for row in range(1, rows + 1) for column in range(1, columns + 1)]


def _labware_by_label(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for item in ir.get("labware") or []:
        if isinstance(item, dict) and item.get("label"):
            out[str(item["label"])] = item
    return out


def _labware_spec(item: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(item.get(key) or "") for key in ("catalog", "label", "python_class")).lower()
    rows = _int_value(item.get("rows"))
    columns = _int_value(item.get("columns"))
    wells = _int_value(item.get("wells") or item.get("well_count"))
    if rows is None or columns is None:
        if "384" in text:
            rows, columns = 16, 24
        elif "1536" in text:
            rows, columns = 32, 48
        elif "24" in text and "tube" not in text:
            rows, columns = 4, 6
        elif any(token in text for token in ("trough", "reservoir", "waste")):
            rows, columns = 1, 1
        elif wells == 1:
            rows, columns = 1, 1
        else:
            rows, columns = 8, 12
    max_volume = _first_number(
        item,
        ("max_volume_ul", "max_well_volume_ul", "well_max_volume_ul", "capacity_ul"),
    )
    if max_volume is None:
        if any(token in text for token in ("trough", "reservoir")):
            max_volume = 25000.0
        elif "384" in text:
            max_volume = 120.0
        else:
            max_volume = 200.0
    dead_volume = _first_number(
        item,
        ("dead_volume_ul", "min_volume_ul", "residual_volume_ul", "dead_volume"),
    )
    return {
        "rows": rows,
        "columns": columns,
        "max_volume_ul": max_volume,
        "dead_volume_ul": dead_volume or 0.0,
    }


def _labware_summary(
    specs: dict[str, dict[str, Any]],
    wells: dict[tuple[str, str], WellState],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, spec in specs.items():
        states = [state for (labware, _well), state in wells.items() if labware == label]
        known = [state.volume_ul for state in states if state.known and state.volume_ul is not None]
        out[label] = {
            "rows": spec.get("rows"),
            "columns": spec.get("columns"),
            "max_volume_ul": spec.get("max_volume_ul"),
            "dead_volume_ul": spec.get("dead_volume_ul"),
            "known_well_count": len(known),
            "unknown_well_count": len(states) - len(known),
            "final_min_volume_ul": min(known) if known else None,
            "final_max_volume_ul": max(known) if known else None,
        }
    return out


def _transfer_record(
    step: dict[str, Any],
    operation: str,
    labware: str,
    well: str,
    volume: float,
    before: float | None,
    after: float | None,
    active_tip: dict[str, Any],
) -> dict[str, Any]:
    return {
        "step_id": step.get("id"),
        "index": step.get("index"),
        "operation": operation,
        "labware": labware,
        "well": well,
        "volume_ul": volume,
        "before_ul": before,
        "after_ul": after,
        "tip_after_ul": active_tip.get("volume_ul") if active_tip.get("known") else None,
    }


def _step_issue(
    step: dict[str, Any],
    reason: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "step_id": step.get("id"),
        "index": step.get("index"),
        "operation": step.get("operation"),
        "target_labware": step.get("target_labware"),
        "reason": reason,
        "message": message,
    }
    if extra:
        payload.update(extra)
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _summary(
    status: str,
    failures: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    transfers: list[dict[str, Any]],
) -> str:
    if status == "failed":
        return f"{len(failures)} liquid-state failure(s) found before script generation."
    if status == "needs_user":
        return f"{len(unresolved)} liquid-state value(s) need user confirmation before final generation."
    if warnings:
        return f"Tracked {len(transfers)} liquid movement(s) with {len(warnings)} warning(s)."
    return f"Tracked {len(transfers)} liquid movement(s); no liquid-state failures found."


def _checked_step_count(ir: dict[str, Any]) -> int:
    return sum(1 for step in ir.get("steps") or [] if isinstance(step, dict) and step.get("operation") in VOLUME_OPERATIONS)


def _step_labware(step: dict[str, Any]) -> str:
    return str(step.get("target_labware") or step.get("source_labware") or step.get("destination_labware") or "")


def _is_destination_labware(item: dict[str, Any]) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("role", "label", "name", "id")).lower()
    return any(token in text for token in ("destination", "dest", "output", "receiver"))


def _is_aspirate(operation: str) -> bool:
    return operation in {Operation.ASPIRATE.value, Operation.LIHA_ASPIRATE.value}


def _is_dispense(operation: str) -> bool:
    return operation in {Operation.DISPENSE.value, Operation.LIHA_DISPENSE.value}


def _is_mix(operation: str) -> bool:
    return operation in {Operation.MIX.value, Operation.MCA384_MIX.value, Operation.LIHA_MIX.value}


def _is_tip_pickup(operation: str) -> bool:
    return operation in {Operation.PICK_UP_TIPS.value, Operation.MCA384_GET_TIPS.value, Operation.LIHA_GET_TIPS.value}


def _is_tip_release(operation: str) -> bool:
    return operation in {Operation.SET_TIPS_BACK.value, Operation.DROP_TIPS.value, Operation.MCA384_DROP_TIPS.value, Operation.LIHA_DROP_TIPS.value}


def _parameter(step: dict[str, Any], key: str) -> Any:
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    return step.get(key) if step.get(key) not in (None, "", []) else params.get(key)


def _tip_capacity_ul(labware: dict[str, Any], step: dict[str, Any]) -> float | None:
    for value in (
        labware.get("capacity_ul"),
        labware.get("tip_capacity_ul"),
        _parameter(step, "capacity_ul"),
        _parameter(step, "tip_capacity_ul"),
    ):
        number = _number(value)
        if number is not None:
            return number
    text = " ".join(str(value) for value in (labware.get("catalog"), labware.get("label")) if value)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:u\s*l|ul)", text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def _first_number(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _number(item.get(key))
        if number is not None:
            return number
    return None


def _resolve_volume_ul(value: Any, ir: dict[str, Any]) -> float | None:
    number = _number(value)
    if number is not None:
        return number
    if not isinstance(value, str) or not value.strip():
        return None
    variable_name = value.strip()
    for item in ir.get("variables") or []:
        if not isinstance(item, dict) or str(item.get("name") or "") != variable_name:
            continue
        resolved = _number(item.get("value"))
        if resolved is not None:
            return resolved
        defaults = item.get("default_values")
        if isinstance(defaults, list) and defaults:
            resolved = _number(defaults[0])
            if resolved is not None:
                return resolved
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _int_value(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _parse_well(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", value.strip())
    if not match:
        return None
    row = 0
    for char in match.group(1).upper():
        row = row * 26 + (ord(char) - ord("A") + 1)
    return row, int(match.group(2))


def _row_name(index: int) -> str:
    chars = []
    while index:
        index, remainder = divmod(index - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def _dedupe(values: list[str]) -> list[str]:
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    return out
