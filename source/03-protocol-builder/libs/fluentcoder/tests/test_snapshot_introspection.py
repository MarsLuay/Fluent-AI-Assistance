"""v1 acceptance: snapshot introspection works on a re-authored protocol.


Verifies that after `wt.simulate()`, every step's snapshot reflects the
correct twin state — well layers, tip flow, and is_magnetized stacking.
"""

from __future__ import annotations
from fluentcoder.simulator.options import SimulationOptions


from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import (  # noqa: E402
    MCA100Box, MagnetRack, Plate96, Reagent, Worktable,
)
from fluentcoder.catalog.catalog import index_exists  # noqa: E402
from fluentcoder.simulator.snapshots import fold_snapshot_deltas  # noqa: E402


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_simple_transfer_layers_flow() -> None:
    """The 20µL Input gDNA layer flows source → tips → dest."""
    from examples.simple_transfer import build_worktable

    wt = build_worktable()
    wt.simulate()

    assert wt.snapshots, "simulate() should populate snapshots"

    # First snapshot is the AddLabware for SourcePlate. Source is full of input_dna.
    src_first = wt.snapshots[0].labware("SourcePlate")
    assert src_first.well("A1").volume_ul == 50.0
    assert len(src_first.well("A1").layers) == 1
    assert src_first.well("A1").layers[0].reagent.name == "Input gDNA"

    # Find the snapshot right after Aspirate (head holds 20µL per tip; source loses 20µL).
    aspirate_snap = next(
        s for s in wt.snapshots if type(s.step).__name__ == "AspirateStep"
    )
    src_after_asp = aspirate_snap.labware("SourcePlate")
    dst_after_asp = aspirate_snap.labware("DestPlate")
    assert src_after_asp.well("A1").volume_ul == 30.0
    assert dst_after_asp.well("A1").volume_ul == 0.0
    assert len(aspirate_snap.mca_tips) == 96
    assert aspirate_snap.mca_tips[0].volume_ul == 20.0
    assert aspirate_snap.mca_tips[0].layers[0].reagent.name == "Input gDNA"

    # Snapshot after Dispense: tips empty, dest holds 20µL.
    dispense_snap = next(
        s for s in wt.snapshots if type(s.step).__name__ == "DispenseStep"
    )
    src_after_disp = dispense_snap.labware("SourcePlate")
    dst_after_disp = dispense_snap.labware("DestPlate")
    assert src_after_disp.well("A1").volume_ul == 30.0
    assert dst_after_disp.well("A1").volume_ul == 20.0
    assert dispense_snap.mca_tips[0].volume_ul == 0.0


def test_magnetized_state_toggles_with_gripper_stack() -> None:
    """is_magnetized flips True when stacked onto a MagnetRack, False after move-off."""
    wt = Worktable(name="Magnet stack test")
    wt.group("Setup")
    plate = wt.place(Plate96("Plate", catalog="96 Well Flat"), "Nest", 1)
    rack = wt.place(MagnetRack("Mag", catalog="24 Magnet Plate"), "Nest", 7)

    assert plate.is_magnetized is False

    wt.group("Engage magnet")
    wt.gripper.move(plate, onto=rack)

    wt.group("Disengage")
    wt.gripper.move(plate, to=("Nest", 1))

    wt.simulate()

    # Walk snapshots: find the move-onto-rack and move-off transitions.
    transfer_snaps = [
        s for s in wt.snapshots if type(s.step).__name__ == "RgaTransferLabwareStep"
    ]
    assert len(transfer_snaps) == 2

    # After the first transfer (onto rack), plate should be magnetized.
    plate_on_rack = transfer_snaps[0].labware("Plate")
    assert plate_on_rack.is_magnetized is True
    # The plate's slot should match the rack's.
    assert plate_on_rack.slot == ("Nest", 7)

    # After the second transfer (back to Nest 1), no longer magnetized.
    plate_off_rack = transfer_snaps[1].labware("Plate")
    assert plate_off_rack.is_magnetized is False
    assert plate_off_rack.slot == ("Nest", 1)


def test_simulate_preserves_author_slot_map_and_well_volumes() -> None:
    """simulate() must not mutate author-side deck layout or liquid state."""
    wt = Worktable(name="author isolation")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    src.fill_all(Reagent("Buffer"), 50.0)

    before_slot_map = {
        slot: [lw.label for lw in stack]
        for slot, stack in wt.slot_map.items()
    }
    before_volumes = {
        lw.label: {addr: well.volume_ul for addr, well in lw.wells.items()}
        for stack in wt.slot_map.values()
        for lw in stack
        if lw.wells
    }
    before_tip_count = tips.available_tip_count
    before_src_slot = src.slot

    wt.group("Transfer")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    wt.mca96.aspirate(src, 20.0, liquid_class="Water Free Single")
    wt.mca96.dispense(dst, 20.0, liquid_class="Water Free Single")
    wt.mca96.return_tips()

    wt.simulate()

    after_slot_map = {
        slot: [lw.label for lw in stack]
        for slot, stack in wt.slot_map.items()
    }
    after_volumes = {
        lw.label: {addr: well.volume_ul for addr, well in lw.wells.items()}
        for stack in wt.slot_map.values()
        for lw in stack
        if lw.wells
    }

    assert after_slot_map == before_slot_map
    assert after_volumes == before_volumes
    assert tips.available_tip_count == before_tip_count
    assert src.slot == before_src_slot
    assert src.well("A1").volume_ul == 50.0
    assert dst.well("A1").volume_ul == 0.0

    final_src = wt.snapshots[-1].labware("Source")
    final_dst = wt.snapshots[-1].labware("Dest")
    assert final_src.well("A1").volume_ul == 30.0
    assert final_dst.well("A1").volume_ul == 20.0


def test_record_snapshots_false_keeps_one_snapshot_with_correct_final_state() -> None:
    """record_snapshots=False avoids per-step deepcopy but final twin state matches full mode."""
    wt_full = Worktable(name="snapshot mode full")
    wt_final = Worktable(name="snapshot mode final")
    for wt in (wt_full, wt_final):
        wt.group("Setup")
        src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
        dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
        tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
        src.fill_all(Reagent("Buffer"), 50.0)
        wt.group("Transfer")
        wt.mca96.mount_adapter()
        wt.mca96.pick_up(tips)
        wt.mca96.aspirate(src, 20.0, liquid_class="Water Free Single")
        wt.mca96.dispense(dst, 20.0, liquid_class="Water Free Single")
        wt.mca96.return_tips()

    wt_full.simulate()
    wt_final.simulate(SimulationOptions(record_snapshots=False))

    assert len(wt_full.snapshots) > 1
    assert len(wt_final.snapshots) == 1
    assert wt_final.simulation_report.total_executed_steps == wt_full.simulation_report.total_executed_steps

    full_final = wt_full.snapshots[-1]
    only_final = wt_final.snapshots[-1]
    assert only_final.step_index == full_final.step_index
    assert type(only_final.step).__name__ == type(full_final.step).__name__
    assert only_final.labware("Source").well("A1").volume_ul == full_final.labware("Source").well("A1").volume_ul
    assert only_final.labware("Dest").well("A1").volume_ul == full_final.labware("Dest").well("A1").volume_ul
    assert only_final.mca_tips == full_final.mca_tips


def _build_transfer_worktable(name: str) -> Worktable:
    wt = Worktable(name=name)
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    src.fill_all(Reagent("Buffer"), 50.0)
    wt.group("Transfer")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    wt.mca96.aspirate(src, 20.0, liquid_class="Water Free Single")
    wt.mca96.dispense(dst, 20.0, liquid_class="Water Free Single")
    wt.mca96.return_tips()
    return wt


def _slot_map_labware_count(snapshots) -> int:
    return sum(
        len(stack)
        for snap in snapshots
        for stack in snap.slot_map.values()
    )


def test_delta_snapshot_mode_records_per_step_diffs_with_correct_final_state() -> None:
    """Delta mode keeps N lightweight snapshots whose folded state matches full mode."""
    wt_full = _build_transfer_worktable("snapshot mode full")
    wt_delta = _build_transfer_worktable("snapshot mode delta")

    wt_full.simulate()
    wt_delta.simulate(SimulationOptions(record_snapshots="delta"))

    assert len(wt_delta.snapshots) == len(wt_full.snapshots)
    assert len(wt_delta.snapshots) > 1
    assert all(snap.delta is not None for snap in wt_delta.snapshots)
    assert all(not snap.slot_map for snap in wt_delta.snapshots)
    assert _slot_map_labware_count(wt_delta.snapshots) == 0
    assert _slot_map_labware_count(wt_full.snapshots) > 0

    folded = fold_snapshot_deltas(wt_delta.snapshots)
    full_final = wt_full.snapshots[-1]
    assert folded["labware"]["Source"]["wells"]["A1"]["volume_ul"] == (
        full_final.labware("Source").well("A1").volume_ul
    )
    assert folded["labware"]["Dest"]["wells"]["A1"]["volume_ul"] == (
        full_final.labware("Dest").well("A1").volume_ul
    )
    assert folded["mca_tips"] == []

    assert wt_delta.simulation_report.total_executed_steps == (
        wt_full.simulation_report.total_executed_steps
    )
    assert all(snap.to_dict()["snapshot_mode"] == "delta" for snap in wt_delta.snapshots)


def test_snapshot_mode_delta_alias_matches_record_snapshots_delta() -> None:
    """snapshot_mode='delta' is equivalent to record_snapshots='delta'."""
    wt_kw = _build_transfer_worktable("delta via record_snapshots")
    wt_mode = _build_transfer_worktable("delta via snapshot_mode")

    wt_kw.simulate(SimulationOptions(record_snapshots="delta"))
    wt_mode.simulate(SimulationOptions(snapshot_mode="delta"))

    assert len(wt_kw.snapshots) == len(wt_mode.snapshots)
    for left, right in zip(wt_kw.snapshots, wt_mode.snapshots):
        assert left.delta == right.delta


def test_delta_mode_uses_less_payload_than_full_on_long_protocol() -> None:
    """Many repeated transfers: delta snapshots avoid deep-copied slot maps."""
    def build_long(name: str) -> Worktable:
        wt = Worktable(name=name)
        wt.group("Setup")
        src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
        dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
        tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
        src.fill_all(Reagent("Buffer"), 5000.0)
        wt.group("Transfer")
        wt.mca96.mount_adapter()
        wt.mca96.pick_up(tips)
        for _ in range(40):
            wt.mca96.aspirate(src, 5.0, liquid_class="Water Free Single")
            wt.mca96.dispense(dst, 5.0, liquid_class="Water Free Single")
        wt.mca96.return_tips()
        return wt

    wt_full = build_long("long full")
    wt_delta = build_long("long delta")
    wt_full.simulate()
    wt_delta.simulate(SimulationOptions(record_snapshots="delta"))

    assert len(wt_delta.snapshots) == len(wt_full.snapshots)
    assert _slot_map_labware_count(wt_full.snapshots) > len(wt_full.snapshots)
    assert _slot_map_labware_count(wt_delta.snapshots) == 0

    folded = fold_snapshot_deltas(wt_delta.snapshots)
    full_final = wt_full.snapshots[-1]
    assert folded["labware"]["Source"]["wells"]["A1"]["volume_ul"] == (
        full_final.labware("Source").well("A1").volume_ul
    )
    assert folded["labware"]["Dest"]["wells"]["A1"]["volume_ul"] == (
        full_final.labware("Dest").well("A1").volume_ul
    )

