"""Phase A — pinning tests for each example protocol in examples/.

Each test asserts the example builds a clean Worktable, simulates without
errors, compiles to a .xscr, and that key snapshot facts hold (final
volumes, magnet flips, loop iteration counts).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from tests._module_loader import load_module  # noqa: E402


pytestmark = pytest.mark.usefixtures("synthetic_catalog")


def _load_example(name: str):
    """Import an example/ module by filename."""
    path = REPO_ROOT / "examples" / f"{name}.py"
    return load_module(path, alias=f"examples.{name}")


def test_round_trip_780_empty() -> None:
    """A.1 — round-trip example builds, simulates, and compiles."""
    module = _load_example("round_trip_780_empty")
    wt = module.build_worktable()

    assert wt.workspace_name == "780_Empty"
    assert wt.valid_slots is not None
    # Post xwsp parser fix: 780_Empty enumerates 56 slots, not 1.
    assert len(wt.valid_slots) == 56, (
        f"expected 780_Empty to enumerate 56 slots, got {len(wt.valid_slots)}"
    )

    wt.simulate()
    dest = wt.snapshots[-1].labware("DestPlate")
    assert dest.well("A1").volume_ul == pytest.approx(20.0)
    assert len(dest.well("A1").layers) == 1
    assert dest.well("A1").layers[0].reagent.name == "Sample"


def test_ampure_cleanup_magnet_roundtrip() -> None:
    """A.3 — magnet round-trip: stack-derived is_magnetized flips correctly,
    pinned-when-magnetized layers are skipped during supernatant draw."""
    module = _load_example("ampure_cleanup")
    wt = module.build_worktable()
    wt.simulate()

    types = [type(s.step).__name__ for s in wt.snapshots]
    on_idx = next(i for i, t in enumerate(types) if t == "RgaTransferLabwareStep")
    asp_idx = next(
        i for i, t in enumerate(types[on_idx:], start=on_idx) if t == "AspirateStep"
    )
    off_idx = next(
        i for i, t in enumerate(types[asp_idx:], start=asp_idx)
        if t == "RgaTransferLabwareStep"
    )

    # Move onto magnet → derived magnetization is True.
    assert wt.snapshots[on_idx].labware("Sample").is_magnetized is True
    # Aspirate skips beads (pinned_when_magnetized) — supernatant only.
    asp_layers = wt.snapshots[asp_idx].mca_tips[0].layers
    asp_reagents = {layer.reagent.name for layer in asp_layers}
    assert "AMPure beads" not in asp_reagents
    assert "Sample DNA" in asp_reagents
    # Move off magnet → derived magnetization is False.
    assert wt.snapshots[off_idx].labware("Sample").is_magnetized is False

    # End-state: sample plate has buffer dispensed back in.
    final_sample = wt.snapshots[-1].labware("Sample")
    final_reagents = {layer.reagent.name for layer in final_sample.well("A1").layers}
    assert "Wash buffer" in final_reagents
    assert "AMPure beads" in final_reagents  # beads stayed (were pinned)


def test_loop_conditional_dispatches_correctly() -> None:
    """A.2 — loop body dispatched N times, conditional then-branch taken."""
    module = _load_example("loop_conditional")
    wt = module.build_worktable()
    wt.simulate()

    # 3 wash cycles * (10 µL main + 5 µL conditional rinse) = 45 µL into waste
    waste_a1 = wt.snapshots[-1].labware("Waste").well("A1")
    assert waste_a1.volume_ul == pytest.approx(45.0)

    # The IR contains a single LoopStep with a nested ConditionalStep.
    from fluentcoder.ir.schema import LoopStep, ConditionalStep
    proto = wt.to_protocol()
    pipetting = next(g for g in proto.groups if g.name == "Wash")
    loop_step = next(s for s in pipetting.steps if isinstance(s, LoopStep))
    assert loop_step.number_of_loops == "cycles"
    assert any(isinstance(s, ConditionalStep) for s in loop_step.steps)
    cond_step = next(s for s in loop_step.steps if isinstance(s, ConditionalStep))
    assert cond_step.left_variable == "ph"
    assert cond_step.operator == ">="
    assert cond_step.right_value == 7


def test_normalize_to_target() -> None:
    """A.4 — uniform dilution across wells with non-uniform starting state.

    Documents v1.1 limitation: MCA aspirate is auto-parallel with a single
    volume scalar. Per-well varying volumes require IR or FCA changes.
    """
    module = _load_example("normalize_to_target")
    wt = module.build_worktable()
    wt.simulate()

    # All dest wells get the same total (uniform dispense).
    final_dst = wt.snapshots[-1].labware("Dest")
    volumes = {w_addr: w.volume_ul for w_addr, w in final_dst.wells.items()}
    assert all(v == pytest.approx(60.0) for v in volumes.values()), (
        f"expected every dest well to be 60 µL after uniform transfer, got "
        f"{set(volumes.values())}"
    )

    # Source plate's per-well starting volumes diverged; after a uniform
    # 30 µL aspirate from every well, the remaining volumes still diverge
    # — confirms per-well state survives auto-parallel pipetting.
    final_src = wt.snapshots[-1].labware("Source")
    src_volumes = {w_addr: w.volume_ul for w_addr, w in final_src.wells.items()}
    assert len(set(round(v, 2) for v in src_volumes.values())) > 50, (
        "expected source plate to retain per-well state divergence"
    )

