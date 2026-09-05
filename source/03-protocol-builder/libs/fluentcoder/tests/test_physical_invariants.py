"""v1 acceptance: physical invariants raise on impossible operations.

These are physics, not domain rules. No keyword lists, no scenario logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import (  # noqa: E402
    CannotAspirateError, InsufficientVolumeError, MissingAdapterError,
    MissingTipsError, OverdrawError,
    MCA100Box, MagnetRack, Plate96, Reagent, Worktable,
)


def _build_minimal_worktable() -> tuple[Worktable, object, object, object]:
    wt = Worktable(name="Inv test")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    return wt, src, dst, tips


def test_occupied_slot_raises_at_authoring() -> None:
    """place() into an already-occupied slot raises OccupiedSlotError-equivalent."""
    wt = Worktable()
    wt.group("Setup")
    wt.place(Plate96("A", catalog="96 Well Flat"), "Nest", 1)
    with pytest.raises(ValueError, match="already occupied"):
        wt.place(Plate96("B", catalog="96 Well Flat"), "Nest", 1)


def test_aspirate_without_adapter_raises() -> None:
    wt, src, dst, tips = _build_minimal_worktable()
    wt.group("Bad")
    # No mount_adapter call.
    wt.mca96.pick_up(tips)
    wt.mca96.aspirate(src, 20.0, liquid_class="Water Free Single")
    with pytest.raises(MissingAdapterError):
        wt.simulate()


def test_pickup_without_adapter_raises() -> None:
    wt, src, dst, tips = _build_minimal_worktable()
    wt.group("Bad")
    wt.mca96.pick_up(tips)
    with pytest.raises(MissingAdapterError):
        wt.simulate()


def test_aspirate_without_tips_raises() -> None:
    wt, src, dst, tips = _build_minimal_worktable()
    wt.group("Bad")
    wt.mca96.mount_adapter()
    # No pick_up.
    wt.mca96.aspirate(src, 20.0, liquid_class="Water Free Single")
    with pytest.raises(MissingTipsError):
        wt.simulate()


def test_insufficient_volume_raises() -> None:
    wt, src, dst, tips = _build_minimal_worktable()
    src.fill_all(Reagent("Buffer"), 5.0)  # only 5 µL per well
    wt.group("Pipette")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    wt.mca96.aspirate(src, 20.0, liquid_class="Water Free Single")
    with pytest.raises(InsufficientVolumeError):
        wt.simulate()


def test_dispense_more_than_tip_holds_raises() -> None:
    wt, src, dst, tips = _build_minimal_worktable()
    src.fill_all(Reagent("Buffer"), 50.0)
    wt.group("Pipette")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    wt.mca96.aspirate(src, 10.0, liquid_class="Water Free Single")
    wt.mca96.dispense(dst, 30.0, liquid_class="Water Free Single")
    with pytest.raises(OverdrawError):
        wt.simulate()


def test_pinned_aspirate_on_magnetized_plate_raises() -> None:
    """Aspirating a pinned-only well from a plate stacked on a magnet rack
    raises CannotAspirateError (magnet-aware aspirate skips pinned layers)."""
    wt = Worktable()
    wt.group("Setup")
    plate = wt.place(Plate96("Plate", catalog="96 Well Flat"), "Nest", 1)
    rack = wt.place(MagnetRack("Mag", catalog="24 Magnet Plate"), "Nest", 7)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    beads = Reagent("AMPure beads", pinned_when_magnetized=True)
    plate.fill_all(beads, 100.0)

    wt.group("Engage")
    wt.gripper.move(plate, onto=rack)

    wt.group("Try to aspirate beads off magnet")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    wt.mca96.aspirate(plate, 20.0, liquid_class="Water Free Single")

    with pytest.raises(CannotAspirateError, match="pinned on magnet"):
        wt.simulate()

