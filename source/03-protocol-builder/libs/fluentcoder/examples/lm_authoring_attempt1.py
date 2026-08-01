from tecanlab import Worktable, Reagent, Plate96, MCA200Box, FCA1000Box, Trough100mL, MagnetRack, WasteChute

def build_worktable() -> Worktable:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="AMPure XP PCR Cleanup 96-Well",
        comment="PCR cleanup with AMPure XP beads - FCA trough dispensing, MCA mixing/aspiration",
    )

    # --- Variables (mandatory first group) ---
    wt.declare_variable("VOLUME_SAMPLE_UL", 20.0)
    wt.set_sim_value("VOLUME_SAMPLE_UL", 20.0)
    VOLUME_SAMPLE_UL = "VOLUME_SAMPLE_UL"

    wt.declare_variable("VOLUME_BEADS_UL", 36.0)
    wt.set_sim_value("VOLUME_BEADS_UL", 36.0)
    VOLUME_BEADS_UL = "VOLUME_BEADS_UL"

    wt.declare_variable("VOLUME_SUPERNATANT_UL", 51.0)
    wt.set_sim_value("VOLUME_SUPERNATANT_UL", 51.0)
    VOLUME_SUPERNATANT_UL = "VOLUME_SUPERNATANT_UL"

    wt.declare_variable("VOLUME_ETHANOL_UL", 200.0)
    wt.set_sim_value("VOLUME_ETHANOL_UL", 200.0)
    VOLUME_ETHANOL_UL = "VOLUME_ETHANOL_UL"

    wt.declare_variable("VOLUME_ELUTION_UL", 40.0)
    wt.set_sim_value("VOLUME_ELUTION_UL", 40.0)
    VOLUME_ELUTION_UL = "VOLUME_ELUTION_UL"

    wt.declare_variable("SOURCE_FILL_BEADS_UL", 3802.0)
    wt.set_sim_value("SOURCE_FILL_BEADS_UL", 3802.0)
    SOURCE_FILL_BEADS_UL = "SOURCE_FILL_BEADS_UL"

    wt.declare_variable("SOURCE_FILL_ETHANOL_UL", 42240.0)
    wt.set_sim_value("SOURCE_FILL_ETHANOL_UL", 42240.0)
    SOURCE_FILL_ETHANOL_UL = "SOURCE_FILL_ETHANOL_UL"

    wt.declare_variable("SOURCE_FILL_ELUTION_UL", 4224.0)
    wt.set_sim_value("SOURCE_FILL_ELUTION_UL", 4224.0)
    SOURCE_FILL_ELUTION_UL = "SOURCE_FILL_ELUTION_UL"

    # Liquid class string variables (all default to Water Free Single)
    wt.declare_variable("LIQUID_CLASS_BEADS", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_BEADS", "Water Free Single")
    LIQUID_CLASS_BEADS = "LIQUID_CLASS_BEADS"

    wt.declare_variable("LIQUID_CLASS_ETHANOL", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_ETHANOL", "Water Free Single")
    LIQUID_CLASS_ETHANOL = "LIQUID_CLASS_ETHANOL"

    wt.declare_variable("LIQUID_CLASS_ELUTION", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_ELUTION", "Water Free Single")
    LIQUID_CLASS_ELUTION = "LIQUID_CLASS_ELUTION"

    wt.declare_variable("LIQUID_CLASS_MIX", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_MIX", "Water Free Single")
    LIQUID_CLASS_MIX = "LIQUID_CLASS_MIX"

    wt.declare_variable("LIQUID_CLASS_ASPIRATE", "Water Free Single")
    wt.set_sim_value("LIQUID_CLASS_ASPIRATE", "Water Free Single")
    LIQUID_CLASS_ASPIRATE = "LIQUID_CLASS_ASPIRATE"

    # --- Labware Placement (mandatory second group) ---
    wt.group("Labware Placement")
    source = wt.place(Plate96("SourcePlate", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    dest = wt.place(Plate96("DestPlate (elution plate)", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 2)
    magnet = wt.place(MagnetRack("MagnetPlate", catalog="LV_Alpaqua_A000350"), "Nest61mm_Pos", 3)
    mca_tips = wt.place(MCA200Box("MCATips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 4)
    fca_tips = wt.place(FCA1000Box("FCATips", catalog="FCA, 1000ul"), "Nest61mm_Pos", 5)
    waste = wt.place(WasteChute("WasteChute", catalog="MCA Thru Deck Waste Chute"), "ThruDeckWaste_Pos", 1)
    trough_beads = wt.place(Trough100mL("TroughBeads", catalog="100ml Trough 156mm"), "WS_100ml_1", 1)
    trough_ethanol = wt.place(Trough100mL("TroughEthanol", catalog="100ml Trough 156mm"), "WS_100ml_1", 2)
    trough_elution = wt.place(Trough100mL("TroughElution", catalog="100ml Trough 156mm"), "WS_100ml_1", 3)

    # --- Fill Reagents ---
    wt.group("Fill Reagents")
    pcr_sample = Reagent("PCR_Sample")
    beads_reagent = Reagent("AMPure_XP_Beads")
    ethanol_reagent = Reagent("Ethanol_70")
    elution_reagent = Reagent("Elution_Buffer")

    source.fill_all(pcr_sample, 20.0)
    trough_beads.fill_all(beads_reagent, 3802.0)
    trough_ethanol.fill_all(ethanol_reagent, 42240.0)
    trough_elution.fill_all(elution_reagent, 4224.0)

    # --- Dispense Beads FCA (aspirate from trough, dispense column-wise to plate) ---
    wt.group("Dispense Beads FCA")
    liha = wt.liha
    liha.get_tips(fca_tips)
    with wt.loop(times=12, name="Dispense bead columns", loop_variable="col"):
        liha.aspirate(trough_beads, VOLUME_BEADS_UL, liquid_class=LIQUID_CLASS_BEADS)
        liha.dispense(source, VOLUME_BEADS_UL, liquid_class=LIQUID_CLASS_BEADS, well_offset="(col-1)*8")
    liha.drop_tips()

    # --- Mix And Incubate Binding (MCA pipette mix 10x, then incubate 5 min) ---
    wt.group("Mix And Incubate Binding")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.mix(source, VOLUME_BEADS_UL, cycles=10, liquid_class=LIQUID_CLASS_MIX)
    head.return_tips(mca_tips)
    head.drop_adapter()

    # --- Magnet Separation 1 (move source onto magnet, wait 2 min) ---
    wt.group("Magnet Separation 1")
    wt.gripper.move(source, to=("Nest61mm_Pos", 3))
    wt.wait(120.0)

    # --- Aspirate Supernatant MCA (aspirate ~51 uL leaving 5 uL behind, empty tips to waste) ---
    wt.group("Aspirate Supernatant MCA")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.aspirate(source, VOLUME_SUPERNATANT_UL, liquid_class=LIQUID_CLASS_ASPIRATE)
    head.empty_tips(waste, VOLUME_SUPERNATANT_UL, liquid_class=LIQUID_CLASS_ASPIRATE)
    head.return_tips(mca_tips)
    head.drop_adapter()

    # --- Ethanol Wash 1 (FCA aspirate ethanol from trough, dispense to plate column-wise; MCA aspirate waste) ---
    wt.group("Ethanol Wash 1")
    liha = wt.liha
    liha.get_tips(fca_tips)
    with wt.loop(times=12, name="Dispense ethanol columns", loop_variable="col"):
        liha.aspirate(trough_ethanol, VOLUME_ETHANOL_UL, liquid_class=LIQUID_CLASS_ETHANOL)
        liha.dispense(source, VOLUME_ETHANOL_UL, liquid_class=LIQUID_CLASS_ETHANOL, well_offset="(col-1)*8")
    liha.drop_tips()
    wt.wait(30.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.aspirate(source, VOLUME_ETHANOL_UL, liquid_class=LIQUID_CLASS_ASPIRATE)
    head.empty_tips(waste, VOLUME_ETHANOL_UL, liquid_class=LIQUID_CLASS_ASPIRATE)
    head.return_tips(mca_tips)
    head.drop_adapter()

    # --- Ethanol Wash 2 (repeat) ---
    wt.group("Ethanol Wash 2")
    liha = wt.liha
    liha.get_tips(fca_tips)
    with wt.loop(times=12, name="Dispense ethanol columns", loop_variable="col"):
        liha.aspirate(trough_ethanol, VOLUME_ETHANOL_UL, liquid_class=LIQUID_CLASS_ETHANOL)
        liha.dispense(source, VOLUME_ETHANOL_UL, liquid_class=LIQUID_CLASS_ETHANOL, well_offset="(col-1)*8")
    liha.drop_tips()
    wt.wait(30.0)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.aspirate(source, VOLUME_ETHANOL_UL, liquid_class=LIQUID_CLASS_ASPIRATE)
    head.empty_tips(waste, VOLUME_ETHANOL_UL, liquid_class=LIQUID_CLASS_ASPIRATE)
    head.return_tips(mca_tips)
    head.drop_adapter()

    # --- Dispense Elution Buffer FCA (remove from magnet first, then aspirate/dispense column-wise) ---
    wt.group("Dispense Elution Buffer FCA")
    wt.gripper.move(source, to=("Nest61mm_Pos", 1))

    liha = wt.liha
    liha.get_tips(fca_tips)
    with wt.loop(times=12, name="Dispense elution columns", loop_variable="col"):
        liha.aspirate(trough_elution, VOLUME_ELUTION_UL, liquid_class=LIQUID_CLASS_ELUTION)
        liha.dispense(source, VOLUME_ELUTION_UL, liquid_class=LIQUID_CLASS_ELUTION, well_offset="(col-1)*8")
    liha.drop_tips()

    # --- Mix And Incubate Elution (MCA pipette mix 10x, then incubate 2 min) ---
    wt.group("Mix And Incubate Elution")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.mix(source, VOLUME_ELUTION_UL, cycles=10, liquid_class=LIQUID_CLASS_MIX)
    head.return_tips(mca_tips)
    head.drop_adapter()

    # --- Magnet Separation 2 (move source onto magnet, wait 1 min) ---
    wt.group("Magnet Separation 2")
    wt.gripper.move(source, to=("Nest61mm_Pos", 3))
    wt.wait(60.0)

    # --- Transfer Eluate MCA (transfer eluate from source to dest plate) ---
    wt.group("Transfer Eluate MCA")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(mca_tips)
    head.aspirate(source, VOLUME_ELUTION_UL, liquid_class=LIQUID_CLASS_ELUTION)
    head.dispense(dest, VOLUME_ELUTION_UL, liquid_class=LIQUID_CLASS_ELUTION)
    head.return_tips(mca_tips)
    head.drop_adapter()

    return wt