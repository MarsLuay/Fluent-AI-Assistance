"""Simple transfer — the v1 parity example.

Move 20 µL from one 96-well plate to another using the MCA-96 head.
Authoring is purely OO: Worktable, Plate96, MCA100Box, EvaAdapter, Reagent.
"""

from fluentcoder import (
    Worktable, Reagent,
    Plate96, MCA100Box,
)


def build_worktable() -> Worktable:
    """Build the worktable + protocol IR. Returns the Worktable for inspection."""
    input_dna = Reagent("Input gDNA")

    wt = Worktable.from_workspace(
        "780_Empty",
        auto_place=False,
        protocol_name="Simple transfer",
        comment="Move liquid from one plate to another",
    )

    wt.group("Setup")
    src = wt.place(Plate96("SourcePlate", catalog="96 Well Flat"), "Site", 1)
    wt.place(Plate96("DestPlate", catalog="96 Well Flat"), "Site", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Site", 4)

    src.fill_all(input_dna, 50.0)

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
    out = wt.compile("simple_transfer.xscr")
    print(f"Wrote {out}")
    dest = wt.snapshots[-1].labware("DestPlate")
    print(f"DestPlate A1 layers: {dest.well('A1').layers}")
