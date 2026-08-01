"""AMPure-style cleanup — Phase A.3.

Demonstrates the magnet round-trip: gripper-moving a plate onto a
``MagnetRack`` flips ``is_magnetized`` to True (derived from
``stack_below``), supernatant aspirate skips layers whose reagent is
``pinned_when_magnetized=True``, and gripper-moving the plate off flips
the flag back. There is no explicit magnetize/engage step in the
protocol — the geometry implies the state.

Sample wells are layered: bottom = sample DNA (100 µL), top = AMPure
beads (20 µL, ``pinned_when_magnetized=True``). When magnetized, the top
beads layer is skipped and the supernatant below is drawn instead.

Caveat: the v1.1 simulator's auto-parallel aspirate pairs the 96 MCA
channels with ``labware.wells.values()`` 1:1. That works for plate-to-
plate but not for 96-channels-into-1-trough. This example uses 96-well
plates as the buffer reservoir and waste container so every channel has
a target. Tracked in the gap log.
"""

from fluentcoder import (
    Worktable, Reagent, Layer,
    Plate96, MCA100Box, MagnetRack,
)


def build_worktable() -> Worktable:
    sample = Reagent("Sample DNA")
    beads = Reagent("AMPure beads", pinned_when_magnetized=True)
    buffer = Reagent("Wash buffer")

    wt = Worktable.from_workspace(
        "780_Empty",
        auto_place=False,
        protocol_name="AMPure cleanup",
        comment="Magnet round-trip with pinned beads",
    )

    wt.group("Setup")
    magnet = wt.place(MagnetRack("Magnet", catalog="24 Magnet Plate"), "Site", 7)
    plate = wt.place(Plate96("Sample", catalog="96 Well Flat"), "Site", 1)
    waste = wt.place(Plate96("Waste", catalog="96 Well Flat"), "Site", 2)
    buffer_plate = wt.place(Plate96("Buffer", catalog="96 Well Flat"), "Site", 3)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Site", 5)

    plate.fill_all(sample, 100.0)
    for w in plate.wells.values():
        w.layers.append(Layer(reagent=beads, volume_ul=20.0))
    buffer_plate.fill_all(buffer, 100.0)

    wt.group("Bind to magnet, aspirate supernatant")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)

    wt.gripper.move(plate, onto=magnet)
    head.aspirate(plate, 100.0, liquid_class="Water Free Single")
    head.dispense(waste, 100.0, liquid_class="Water Free Single")
    wt.gripper.move(plate, to=("Site", 1))

    wt.group("Resuspend in buffer")
    head.aspirate(buffer_plate, 50.0, liquid_class="Water Free Single")
    head.dispense(plate, 50.0, liquid_class="Water Free Single")

    head.return_tips(tips)
    head.drop_adapter()

    # Offline compile bindings (explicit — not generation.yaml invent).
    if not wt.device_alias:
        wt.device_alias = "Instrument=1/Device=MCA384:1"
    if not wt.available_id:
        wt.available_id = "USB:TECAN,FLUENT,2203009762/MCA384:1"
    if not wt.rga_module_name:
        wt.rga_module_name = "RGA 1"

    return wt


if __name__ == "__main__":
    wt = build_worktable()
    wt.simulate()
    out = wt.compile("ampure_cleanup.xscr")
    print(f"Wrote {out}")

    types = [type(s.step).__name__ for s in wt.snapshots]
    on_idx = next(i for i, t in enumerate(types) if t == "RgaTransferLabwareStep")
    asp_idx = next(i for i, t in enumerate(types[on_idx:], start=on_idx) if t == "AspirateStep")
    off_idx = next(i for i, t in enumerate(types[asp_idx:], start=asp_idx) if t == "RgaTransferLabwareStep")

    on_plate = wt.snapshots[on_idx].labware("Sample")
    asp_tip = wt.snapshots[asp_idx].mca_tips[0]
    off_plate = wt.snapshots[off_idx].labware("Sample")

    print(f"After move-onto-magnet:  is_magnetized = {on_plate.is_magnetized}")
    print(f"After supernatant draw:  tip[0] reagents = "
          f"{[layer.reagent.name for layer in asp_tip.layers]}")
    print(f"After move-off-magnet:   is_magnetized = {off_plate.is_magnetized}")
