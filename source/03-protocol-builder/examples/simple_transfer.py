"""Small fluentcoder protocol used to test the local Layer 3 pipeline."""

from fluentcoder import MCA100Box, Plate96, Reagent, Worktable


def build_worktable() -> Worktable:
    input_dna = Reagent("Input gDNA")

    wt = Worktable.from_workspace(
        "780_Empty",
        auto_place=False,
        protocol_name="Pipeline simple transfer",
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

    return wt


if __name__ == "__main__":
    wt = build_worktable()
    wt.simulate()
    out = wt.compile("pipeline_simple_transfer.xscr")
    print(f"Wrote {out}")
