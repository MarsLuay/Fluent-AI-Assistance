"""Loop + conditional — Phase A.2.

Three-cycle wash with a sim-time-gated extra rinse. Demonstrates the
``with wt.loop(times=...)`` and ``with wt.conditional(left=, op=, right=)``
context managers — both emit nested IR ``LoopStep`` / ``ConditionalStep``
constructs that the simulator dispatches and the renderer turns into
FluentControl loop/conditional XML blocks.

The loop count and conditional predicate read from runtime variables;
``set_sim_value`` provides the values the simulator walks with.
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
        protocol_name="Loop + conditional",
        comment="3-cycle wash with sim-time-gated extra rinse",
    )

    wt.declare_variable("cycles", 3)
    wt.declare_variable("ph", 7)
    wt.set_sim_value("cycles", 3)
    wt.set_sim_value("ph", 7)

    wt.group("Setup")
    src = wt.place(Plate96("Sample", catalog="96 Well Flat"), "Site", 1)
    waste = wt.place(Plate96("Waste", catalog="96 Well Flat"), "Site", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Site", 3)
    src.fill_all(sample, 200.0)

    wt.group("Wash")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)

    with wt.loop(times="cycles", name="Wash cycles"):
        head.aspirate(src, 10.0, liquid_class="Water Free Single")
        head.dispense(waste, 10.0, liquid_class="Water Free Single")
        with wt.conditional(left="ph", op=">=", right=7, name="Extra rinse if pH high"):
            head.aspirate(src, 5.0, liquid_class="Water Free Single")
            head.dispense(waste, 5.0, liquid_class="Water Free Single")

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
    out = wt.compile("loop_conditional.xscr")
    print(f"Wrote {out}")

    waste_a1 = wt.snapshots[-1].labware("Waste").well("A1")
    print(f"Waste A1 final volume: {waste_a1.volume_ul} (expected 3 * (10 + 5) = 45)")
    print(f"Snapshots: {len(wt.snapshots)}")
