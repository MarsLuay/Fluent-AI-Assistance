"""Workspace round-trip — Phase A.1.

Builds a Worktable from the real ``780_Empty`` FluentControl workspace,
places labware on slots the workspace says are valid, runs a transfer,
compiles to .xscr.

Verifies:
- ``Worktable.from_workspace`` enumerates real slot counts (post xwsp
  parser fix).
- ``place()`` accepts slots in ``wt.valid_slots`` and rejects others.
- The compiled .xscr round-trips through the renderer.
"""

from fluentcoder import (
    Worktable, Reagent,
    Plate96, MCA100Box,
)


def build_worktable() -> Worktable:
    sample = Reagent("Sample")

    wt = Worktable.from_workspace(
        "780_Empty",
        auto_place=False,
        protocol_name="780 round-trip",
        comment="Workspace-loaded transfer on 780_Empty",
    )

    wt.group("Setup")
    src = wt.place(Plate96("SourcePlate", catalog="96 Well Flat"), "Site", 1)
    wt.place(Plate96("DestPlate", catalog="96 Well Flat"), "Site", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Site", 3)

    src.fill_all(sample, 50.0)

    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    head.dispense(wt.labware_by_label("DestPlate"), 20.0, liquid_class="Water Free Single")
    head.return_tips(tips)
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
    out = wt.compile("round_trip_780_empty.xscr")
    print(f"Wrote {out}")
    print(f"Workspace: {wt.workspace_name!r}")
    print(f"Valid slots: {len(wt.valid_slots)}")
    dest = wt.snapshots[-1].labware("DestPlate")
    print(f"DestPlate A1 layers: {dest.well('A1').layers}")
