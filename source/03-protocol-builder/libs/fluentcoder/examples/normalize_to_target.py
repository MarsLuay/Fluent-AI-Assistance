"""Normalization to target — Phase A.4 (with documented limitation).

What real normalization looks like:
    Source plate has varying concentrations per well. To bring every well
    to the same final concentration, dilute each well by a *different*
    volume of buffer — i.e. per-well volume targeting.

What v1.1 can do:
    The MCA-96 head's aspirate / dispense is auto-parallel over
    ``labware.wells.values()`` with a *single* volume scalar — every
    channel pulls/pushes the same amount. Per-well varying volumes
    require either:
      (a) per-well-volume vectors on ``AspirateStep`` / ``DispenseStep``
          (IR + simulator change), or
      (b) an FCA single-channel head (no FCA head class in v1.1; IR
          steps exist but the simulator doesn't dispatch them).

This example therefore models *uniform* normalization (every well
diluted by the same volume), which is still a useful protocol shape:
add a fixed buffer volume to every well of a plate that already has
varying liquid amounts. The well-state divergence between wells exists,
but the dilution math is uniform.

Tracked as a gap. Real per-well normalization will land in v1.2 once
either (a) or (b) is built.
"""

import random

from fluentcoder import (
    Worktable, Reagent,
    Plate96, MCA100Box,
)


def build_worktable() -> Worktable:
    sample = Reagent("Sample stock")
    buffer = Reagent("Dilution buffer")

    wt = Worktable.from_workspace(
        "780_Empty",
        auto_place=False,
        protocol_name="Normalize to target",
        comment="Uniform dilution across wells with non-uniform starting state",
    )

    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Site", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Site", 2)
    buf_plate = wt.place(Plate96("Buffer", catalog="96 Well Flat"), "Site", 3)
    tips_a = wt.place(MCA100Box("TipsA", catalog="MCA96, 100ul, Box"), "Site", 5)
    tips_b = wt.place(MCA100Box("TipsB", catalog="MCA96, 100ul, Box"), "Site", 6)

    # Non-uniform starting state: each source well has a *different* sample
    # volume in [60, 100] µL. Real normalization would compute per-well
    # dilution factors from these.
    rng = random.Random(42)
    for w in src.wells.values():
        w.layers = []  # start empty
        from fluentcoder import Layer
        w.layers.append(Layer(reagent=sample, volume_ul=rng.uniform(60.0, 100.0)))
    buf_plate.fill_all(buffer, 100.0)

    wt.group("Transfer sample")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips_a)
    head.aspirate(src, 30.0, liquid_class="Water Free Single")
    head.dispense(dst, 30.0, liquid_class="Water Free Single")
    head.return_tips(tips_a)

    wt.group("Add buffer")
    head.pick_up(tips_b)
    head.aspirate(buf_plate, 30.0, liquid_class="Water Free Single")
    head.dispense(dst, 30.0, liquid_class="Water Free Single")
    head.return_tips(tips_b)
    head.drop_adapter()

    # Offline compile bindings (explicit — not generation.yaml invent).
    if not wt.device_alias:
        wt.device_alias = "Instrument=1/Device=MCA384:1"
    if not wt.available_id:
        wt.available_id = "USB:TECAN,FLUENT,2203009762/MCA384:1"

    return wt


if __name__ == "__main__":
    wt = build_worktable()
    wt.simulate()
    out = wt.compile("normalize_to_target.xscr")
    print(f"Wrote {out}")

    final_dst = wt.snapshots[-1].labware("Dest")
    a1, h12 = final_dst.well("A1"), final_dst.well("H12")
    print(f"Dest A1:  vol={a1.volume_ul:.1f}  layers={[(l.reagent.name, round(l.volume_ul, 1)) for l in a1.layers]}")
    print(f"Dest H12: vol={h12.volume_ul:.1f}  layers={[(l.reagent.name, round(l.volume_ul, 1)) for l in h12.layers]}")
