"""Per-step snapshots of the twin world."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..ir.schema import Step
    from ..labware.base import Labware
    from ..heads.mca96 import Tip


@dataclass
class Snapshot:
    """Frozen view of the simulator's state after one IR step.

    In ``full`` mode ``slot_map``, ``mca_tips``, ``mca_adapter``, etc. are
    deep-copied at snapshot time so later mutations cannot leak back into
    history. In ``delta`` mode only a lightweight ``delta`` dict is stored
    (changed labware labels, well volumes, tip state vs. the previous step).
    """

    step_index: int
    step: "Step"
    slot_map: dict[tuple[str, int], list["Labware"]]
    mca_adapter_label: Optional[str]
    mca_tips: list["Tip"]
    mca_tip_box_label: Optional[str]
    liha_tips: list["Tip" | None]
    opaque_events: list[dict]
    warnings: list[str]
    delta: Optional[dict[str, Any]] = None

    def labware(self, label: str) -> "Labware":
        for stack in self.slot_map.values():
            for lw in stack:
                if lw.label == label:
                    return lw
        raise KeyError(f"No labware with label {label!r} at step {self.step_index}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "step_index": self.step_index,
            "step_type": type(self.step).__name__,
            "opaque_events": copy.deepcopy(self.opaque_events),
            "warnings": list(self.warnings),
        }
        if self.delta is not None:
            out["delta"] = copy.deepcopy(self.delta)
            out["snapshot_mode"] = "delta"
        else:
            out["snapshot_mode"] = "full"
            out["labware"] = [
                lw.label for stack in self.slot_map.values() for lw in stack
            ]
            out["mca_adapter"] = self.mca_adapter_label
            out["mca_tip_box"] = self.mca_tip_box_label
            out["mca_tip_volume_total_ul"] = sum(t.volume_ul for t in self.mca_tips)
            out["liha_tip_volume_total_ul"] = sum(
                t.volume_ul for t in self.liha_tips if t is not None
            )
        return out


def take_snapshot(
    step_index: int,
    step: "Step",
    slot_map: dict,
    mca_adapter_label: Optional[str],
    mca_tips: list,
    mca_tip_box_label: Optional[str],
    liha_tips: list,
    opaque_events: list[dict],
    warnings: list[str],
) -> Snapshot:
    return Snapshot(
        step_index=step_index,
        step=step,
        slot_map=copy.deepcopy(slot_map),
        mca_adapter_label=mca_adapter_label,
        mca_tips=copy.deepcopy(mca_tips),
        mca_tip_box_label=mca_tip_box_label,
        liha_tips=copy.deepcopy(liha_tips),
        opaque_events=copy.deepcopy(opaque_events),
        warnings=list(warnings),
    )


def take_delta_snapshot(
    step_index: int,
    step: "Step",
    delta: dict[str, Any],
    opaque_events: list[dict],
    warnings: list[str],
) -> Snapshot:
    return Snapshot(
        step_index=step_index,
        step=step,
        slot_map={},
        mca_adapter_label=None,
        mca_tips=[],
        mca_tip_box_label=None,
        liha_tips=[None] * 8,
        opaque_events=copy.deepcopy(opaque_events),
        warnings=list(warnings),
        delta=delta,
    )


def capture_state_fingerprint(
    *,
    slot_map: dict,
    mca_adapter_label: Optional[str],
    mca_tips: list,
    mca_tip_box_label: Optional[str],
    liha_tips: list,
) -> dict[str, Any]:
    from ..labware.tipboxes import TipBox

    labware: dict[str, Any] = {}
    for stack in slot_map.values():
        for lw in stack:
            entry: dict[str, Any] = {
                "slot": list(lw.slot) if lw.slot else None,
                "is_magnetized": lw.is_magnetized,
            }
            if lw.wells:
                wells: dict[str, Any] = {}
                for address, well in lw.wells.items():
                    wells[address] = {
                        "volume_ul": well.volume_ul,
                        "layers": [
                            {
                                "reagent": layer.reagent.name,
                                "volume_ul": layer.volume_ul,
                            }
                            for layer in well.layers
                        ],
                    }
                entry["wells"] = wells
            if isinstance(lw, TipBox):
                entry["tip_box"] = {
                    "available_tip_count": lw.available_tip_count,
                    "used_tip_count": lw.used_tip_count,
                }
            labware[lw.label] = entry
    return {
        "labware": labware,
        "mca_adapter_label": mca_adapter_label,
        "mca_tip_box_label": mca_tip_box_label,
        "mca_tips": _serialize_tips(mca_tips),
        "liha_tips": _serialize_liha_tips(liha_tips),
    }


def compute_state_delta(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    if previous is None:
        return copy.deepcopy(current)

    delta: dict[str, Any] = {}
    prev_labware = previous.get("labware", {})
    curr_labware = current.get("labware", {})
    changed_labware: dict[str, Any] = {}
    for label in sorted(set(prev_labware) | set(curr_labware)):
        prev_entry = prev_labware.get(label)
        curr_entry = curr_labware.get(label)
        if prev_entry == curr_entry:
            continue
        if curr_entry is None:
            changed_labware[label] = None
            continue
        if prev_entry is None:
            changed_labware[label] = copy.deepcopy(curr_entry)
            continue
        lw_delta: dict[str, Any] = {}
        for field in ("slot", "is_magnetized", "tip_box"):
            if prev_entry.get(field) != curr_entry.get(field):
                lw_delta[field] = copy.deepcopy(curr_entry.get(field))
        prev_wells = prev_entry.get("wells", {})
        curr_wells = curr_entry.get("wells", {})
        well_delta: dict[str, Any] = {}
        for address in sorted(set(prev_wells) | set(curr_wells)):
            prev_well = prev_wells.get(address)
            curr_well = curr_wells.get(address)
            if prev_well != curr_well:
                well_delta[address] = copy.deepcopy(curr_well)
        if well_delta:
            lw_delta["wells"] = well_delta
        if lw_delta:
            changed_labware[label] = lw_delta
    if changed_labware:
        delta["labware"] = changed_labware

    for key in ("mca_adapter_label", "mca_tip_box_label", "mca_tips", "liha_tips"):
        prev_val = previous.get(key)
        curr_val = current.get(key)
        if prev_val != curr_val:
            delta[key] = copy.deepcopy(curr_val)
    return delta


def fold_snapshot_deltas(snapshots: list[Snapshot]) -> dict[str, Any]:
    """Reconstruct folded twin state from a sequence of delta snapshots."""
    state: dict[str, Any] = {}
    for snap in snapshots:
        if snap.delta is None:
            continue
        delta = snap.delta
        if not state:
            state = copy.deepcopy(delta)
            state.setdefault("labware", {})
            continue
        for label, lw_delta in (delta.get("labware") or {}).items():
            if lw_delta is None:
                state.setdefault("labware", {}).pop(label, None)
                continue
            existing = state.setdefault("labware", {}).setdefault(label, {})
            for field_name in ("slot", "is_magnetized", "tip_box"):
                if field_name in lw_delta:
                    existing[field_name] = copy.deepcopy(lw_delta[field_name])
            if "wells" in lw_delta:
                wells = existing.setdefault("wells", {})
                wells.update(copy.deepcopy(lw_delta["wells"]))
        for key in ("mca_adapter_label", "mca_tip_box_label", "mca_tips", "liha_tips"):
            if key in delta:
                state[key] = copy.deepcopy(delta[key])
    state.setdefault("labware", {})
    return state


def _serialize_tips(tips: list) -> list[dict[str, Any]]:
    return [
        {
            "capacity_ul": tip.capacity_ul,
            "volume_ul": tip.volume_ul,
            "layers": [
                {"reagent": layer.reagent.name, "volume_ul": layer.volume_ul}
                for layer in tip.layers
            ],
        }
        for tip in tips
    ]


def _serialize_liha_tips(liha_tips: list) -> list[dict[str, Any] | None]:
    return [
        None
        if tip is None
        else {
            "capacity_ul": tip.capacity_ul,
            "volume_ul": tip.volume_ul,
            "layers": [
                {"reagent": layer.reagent.name, "volume_ul": layer.volume_ul}
                for layer in tip.layers
            ],
        }
        for tip in liha_tips
    ]
