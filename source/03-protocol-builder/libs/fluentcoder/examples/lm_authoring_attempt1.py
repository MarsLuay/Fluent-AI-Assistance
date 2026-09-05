from tecanlab import (
    Worktable,
    Reagent,
    Plate96,
    MCA200Box,
    FCA1000Box,
    Trough100mL,
    MagnetRack,
    WasteChute,
)


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="AMPure XP PCR Cleanup 96-Well",
        comment="PCR cleanup with AMPure XP beads - FCA trough dispensing, MCA mixing/aspiration",
    )

    _declare_variables(wt)
    (
        source,
        dest,
        magnet,
        mca_tips,
        fca_tips,
        waste,
        trough_beads,
        trough_ethanol,
        trough_elution,
    ) = _place_labware(wt)
    _fill_reagents(wt, source, trough_beads, trough_ethanol, trough_elution)

    _dispense_beads_fca(wt, fca_tips, trough_beads, source)
    _mix_and_incubate_binding(wt, mca_tips, source)
    _magnet_separation_1(wt, source)
    _aspirate_supernatant_mca(wt, mca_tips, source, waste)
    _ethanol_wash(
        wt, "Ethanol Wash 1", fca_tips, trough_ethanol, source, mca_tips, waste
    )
    _ethanol_wash(
        wt, "Ethanol Wash 2", fca_tips, trough_ethanol, source, mca_tips, waste
    )
    _dispense_elution_buffer_fca(wt, fca_tips, trough_elution, source)
    _mix_and_incubate_elution(wt, mca_tips, source)
    _magnet_separation_2(wt, source)
    _transfer_eluate_mca(wt, mca_tips, source, dest)

    return wt


def _declare_variables(wt: Worktable):
    wt.declare_variable("VOLUME_SAMPLE_UL", 20.0)
    wt.set_sim_value("VOLUME_SAMPLE_UL", 20.0)

    wt.declare_variable("VOLUME_BEADS_UL", 36.0)
    wt.set_sim_value("VOLUME_BEADS_UL", 36.0)

    wt.declare_variable("VOLUME_SUPERNATANT_UL", 51.0)
    wt.set_sim_value("VOLUME_SUPERNATANT_UL", 51.0)

    wt.declare_variable("VOLUME_ETHANOL_UL", 200.0)
    wt.set_sim_value("VOLUME_ETHANOL_UL", 200.0)

    wt.declare_variable("VOLUME_ELUTION_UL", 40.0)
    wt.set_sim_value("VOLUME_ELUTION_UL", 40.0)

    wt.declare_variable("SOURCE_FILL_BEADS_UL", 3802.0)
    wt.set_sim_value("SOURCE_FILL_BEADS_UL", 3802.0)

    wt.declare_variable("SOURCE_FILL_ETHANOL_UL", 42240.0)
    wt.set_sim_value("SOURCE_FILL_ETHANOL_UL", 42240.0)

    wt.declare_variable("SOURCE_FILL_ELUTION_UL", 4224.0)
    wt.set_sim_value("SOURCE_FILL_ELUTION_UL", 4224.0)

    # Liquid class string variables (all default to Water Free Single)
    wt.declare_variable("LIQUID_CLASS_BEADS", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_BEADS", "Water Free Single")

    wt.declare_variable("LIQUID_CLASS_ETHANOL", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_ETHANOL", "Water Free Single")

    wt.declare_variable("LIQUID_CLASS_ELUTION", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_ELUTION", "Water Free Single")

    wt.declare_variable("LIQUID_CLASS_MIX", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_MIX", "Water Free Single")

    wt.declare_variable("LIQUID_CLASS_ASPIRATE", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_ASPIRATE", "Water Free Single")


def _place_labware(wt: Worktable):
    wt.group("Labware Placement")
    source = wt.place(
        Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"),
        "Nest61mm_Pos",
        1,
    )
    dest = wt.place(
        Plate96(
            "DestPlate (elution plate)", catalog="96_ABgene_SuperPlate_Thermo_AB2800"
        ),
        "Nest61mm_Pos",
        2,
    )
    magnet = wt.place(
        MagnetRack("MagnetPlate", catalog="LV_Alpaqua_A000350"), "Nest61mm_Pos", 3
    )
    mca_tips = wt.place(
        MCA200Box("MCATips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 4
    )
    fca_tips = wt.place(FCA1000Box("FCATips", catalog="FCA, 1000ul"), "Nest61mm_Pos", 5)
    waste = wt.place(
        WasteChute("WasteChute", catalog="MCA Thru Deck Waste Chute"),
        "ThruDeckWaste_Pos",
        1,
    )
    trough_beads = wt.place(
        Trough100mL("TroughBeads", catalog="100ml Trough 156mm"), "WS_100ml_1", 1
    )
    trough_ethanol = wt.place(
        Trough100mL("TroughEthanol", catalog="100ml Trough 156mm"), "WS_100ml_1", 2
    )
    trough_elution = wt.place(
        Trough100mL("TroughElution", catalog="100ml Trough 156mm"), "WS_100ml_1", 3
    )
    return (
        source,
        dest,
        magnet,
        mca_tips,
        fca_tips,
        waste,
        trough_beads,
        trough_ethanol,
        trough_elution,
    )


def _fill_reagents(wt: Worktable, source, trough_beads, trough_ethanol, trough_elution):
    wt.group("Fill Reagents")
    pcr_sample = Reagent("PCR_Sample")
    beads_reagent = Reagent("AMPure_XP_Beads")
    ethanol_reagent = Reagent("Ethanol_70")
    elution_reagent = Reagent("Elution_Buffer")

    source.fill_all(pcr_sample, 20.0)
    trough_beads.fill_all(beads_reagent, 3802.0)
    trough_ethanol.fill_all(ethanol_reagent, 42240.0)
    trough_elution.fill_all(elution_reagent, 4224.0)


def _dispense_beads_fca(wt: Worktable, fca_tips, trough_beads, source):
    wt.group("Dispense Beads FCA")
    liha = wt.liha
    liha.get_tips(fca_tips)
    with wt.loop(times=12, name="Dispense bead columns", loop_variable="col"):
        liha.aspirate(
            trough_beads, "VOLUME_BEADS_UL", liquid_class="LIQUID_CLASS_BEADS"
        )
        liha.dispense(
            source,
            "VOLUME_BEADS_UL",
            liquid_class="LIQUID_CLASS_BEADS",
            well_offset="(col-1)*8",
        )
    liha.drop_tips()


def _mix_and_incubate_binding(wt: Worktable, mca_tips, source):
    wt.group("Mix And Incubate Binding")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.mix(source, "VOLUME_BEADS_UL", cycles=10, liquid_class="LIQUID_CLASS_MIX")
    head.return_tips(mca_tips)
    head.drop_adapter()


def _magnet_separation_1(wt: Worktable, source):
    wt.group("Magnet Separation 1")
    wt.gripper.move(source, to=("Nest61mm_Pos", 3))
    wt.wait(120.0)


def _aspirate_supernatant_mca(wt: Worktable, mca_tips, source, waste):
    wt.group("Aspirate Supernatant MCA")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.aspirate(source, "VOLUME_SUPERNATANT_UL", liquid_class="LIQUID_CLASS_ASPIRATE")
    head.empty_tips(
        waste, "VOLUME_SUPERNATANT_UL", liquid_class="LIQUID_CLASS_ASPIRATE"
    )
    head.return_tips(mca_tips)
    head.drop_adapter()


def _ethanol_wash(
    wt: Worktable, group_name: str, fca_tips, trough_ethanol, source, mca_tips, waste
):
    wt.group(group_name)
    liha = wt.liha
    liha.get_tips(fca_tips)
    with wt.loop(times=12, name="Dispense ethanol columns", loop_variable="col"):
        liha.aspirate(
            trough_ethanol, "VOLUME_ETHANOL_UL", liquid_class="LIQUID_CLASS_ETHANOL"
        )
        liha.dispense(
            source,
            "VOLUME_ETHANOL_UL",
            liquid_class="LIQUID_CLASS_ETHANOL",
            well_offset="(col-1)*8",
        )
    liha.drop_tips()
    wt.wait(30.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.aspirate(source, "VOLUME_ETHANOL_UL", liquid_class="LIQUID_CLASS_ASPIRATE")
    head.empty_tips(waste, "VOLUME_ETHANOL_UL", liquid_class="LIQUID_CLASS_ASPIRATE")
    head.return_tips(mca_tips)
    head.drop_adapter()


def _dispense_elution_buffer_fca(wt: Worktable, fca_tips, trough_elution, source):
    wt.group("Dispense Elution Buffer FCA")
    wt.gripper.move(source, to=("Nest61mm_Pos", 1))

    liha = wt.liha
    liha.get_tips(fca_tips)
    with wt.loop(times=12, name="Dispense elution columns", loop_variable="col"):
        liha.aspirate(
            trough_elution, "VOLUME_ELUTION_UL", liquid_class="LIQUID_CLASS_ELUTION"
        )
        liha.dispense(
            source,
            "VOLUME_ELUTION_UL",
            liquid_class="LIQUID_CLASS_ELUTION",
            well_offset="(col-1)*8",
        )
    liha.drop_tips()


def _mix_and_incubate_elution(wt: Worktable, mca_tips, source):
    wt.group("Mix And Incubate Elution")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.mix(source, "VOLUME_ELUTION_UL", cycles=10, liquid_class="LIQUID_CLASS_MIX")
    head.return_tips(mca_tips)
    head.drop_adapter()


def _magnet_separation_2(wt: Worktable, source):
    wt.group("Magnet Separation 2")
    wt.gripper.move(source, to=("Nest61mm_Pos", 3))
    wt.wait(60.0)


def _transfer_eluate_mca(wt: Worktable, mca_tips, source, dest):
    wt.group("Transfer Eluate MCA")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.aspirate(source, "VOLUME_ELUTION_UL", liquid_class="LIQUID_CLASS_ELUTION")
    head.dispense(dest, "VOLUME_ELUTION_UL", liquid_class="LIQUID_CLASS_ELUTION")
    head.return_tips(mca_tips)
    head.drop_adapter()
