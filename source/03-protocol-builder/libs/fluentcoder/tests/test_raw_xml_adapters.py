"""Chunk 5: Raw XML / GenericStep adapter path tests.


Confirms raw preserved commands are either modeled correctly or explicitly opaque.
Each adapter family gets at least one passing test with an unknown-command control.
"""

from __future__ import annotations
from fluentcoder.simulator.options import SimulationOptions


from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import Plate96, Reagent, SimulationError, Worktable  # noqa: E402


# ── Helper to build a raw XML aspirate/dispense fixture ─────────────

def _raw_liha_aspirate(labware: str, volume: float) -> str:
    return (
        f"<Object>"
        f"<LabwareName>{labware}</LabwareName>"
        f"<Volumes><Object><string>{volume}</string></Object></Volumes>"
        f"<LiquidClassName>Water Free Single</LiquidClassName>"
        f"</Object>"
    )


def _raw_liha_dispense(labware: str, volume: float) -> str:
    return (
        f"<Object>"
        f"<LabwareName>{labware}</LabwareName>"
        f"<Volumes><Object><string>{volume}</string></Object></Volumes>"
        f"<LiquidClassName>Water Free Single</LiquidClassName>"
        f"</Object>"
    )


def _raw_liha_mix(labware: str, volume: float, cycles: int = 5) -> str:
    return (
        f"<Object>"
        f"<LabwareName>{labware}</LabwareName>"
        f"<Volumes><Object><string>{volume}</string></Object></Volumes>"
        f"<LiquidClassName>Water Mix</LiquidClassName>"
        f"<Cycles>{cycles}</Cycles>"
        f"</Object>"
    )


def _raw_liha_empty(labware: str, volume: float = 0) -> str:
    return (
        f"<Object>"
        f"<LabwareName>{labware}</LabwareName>"
        f"<Volumes><Object><string>{volume}</string></Object></Volumes>"
        f"</Object>"
    )


def _raw_liha_aspirate_selected(
    labware: str,
    volume: float,
    *,
    selected_wells: str,
    serialized_indexes: str,
) -> str:
    return (
        f"<Object>"
        f"<LabwareName>{labware}</LabwareName>"
        f"<Volumes><Object><string>{volume}</string></Object></Volumes>"
        f"<LiquidClassName>Water Free Single</LiquidClassName>"
        f"<SelectedWellsString>{selected_wells}</SelectedWellsString>"
        f"<SerializedWellIndexes>{serialized_indexes}</SerializedWellIndexes>"
        f"</Object>"
    )


def _raw_mca384_get_tips(labware: str = "Tips") -> str:
    return f"<Object><LabwareName>{labware}</LabwareName></Object>"


def _raw_mca384_drop_tips(labware: str = "Trash") -> str:
    return f"<Object><LabwareName>{labware}</LabwareName></Object>"


def _raw_mca384_move_arm() -> str:
    return "<Object><MovementType>GlobalZTravel</MovementType></Object>"


def _raw_mca384_mix(labware: str, volume: float, cycles: int = 5) -> str:
    return (
        f"<Object>"
        f"<LabwareName>{labware}</LabwareName>"
        f"<Volume>{volume}</Volume>"
        f"<LiquidClassName>Water Mix</LiquidClassName>"
        f"<Cycles>{cycles}</Cycles>"
        f"</Object>"
    )


def _raw_mca384_empty(labware: str, volume: float = 0) -> str:
    return (
        f"<Object>"
        f"<LabwareName>{labware}</LabwareName>"
        f"<Volume>{volume}</Volume>"
        f"</Object>"
    )


# ── LiHa raw XML adapter tests ─────────────────────────────────────

def test_raw_liha_get_tips_adapted() -> None:
    """Raw XML LihaGetTips is adapted to structured step and changes tip state."""
    wt = Worktable(name="raw liha get tips")
    wt.group("Steps")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == 1
    assert report.fully_simulated_steps == 1  # TIP_STATE_CHANGE
    assert report.raw_xml_generic_steps == 1
    assert report.opaque_noop_steps == 0
    # All 8 channels should have tips now
    assert all(t is not None for t in wt.snapshots[-1].liha_tips)


def test_raw_liha_drop_tips_adapted() -> None:
    """Raw XML LihaDropTips clears mounted tips."""
    wt = Worktable(name="raw liha drop tips")
    wt.group("Steps")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step("LihaDropTips", '<Object><LabwareName>Trash</LabwareName></Object>')

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.fully_simulated_steps == 2  # get + drop both TIP_STATE_CHANGE
    assert all(t is None for t in wt.snapshots[-1].liha_tips)


def test_raw_liha_aspirate_adapted() -> None:
    """Raw XML LihaAspirate aspirates liquid from the target labware."""
    wt = Worktable(name="raw liha aspirate")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Raw")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step("LihaAspirate", _raw_liha_aspirate("Source", 10))

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.fully_simulated_steps >= 2  # get_tips + aspirate
    assert report.raw_xml_generic_steps == 2
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(40.0)


def test_raw_liha_selection_string_targets_selected_wells() -> None:
    """Raw XML LiHa selections should move liquid from the addressed wells only."""
    wt = Worktable(name="raw liha selection")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Raw")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step(
        "LihaAspirate",
        _raw_liha_aspirate_selected(
            "Source",
            10,
            selected_wells="A2 - H2",
            serialized_indexes="8>1>15;",
        ),
    )

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(50.0)
    assert report.final_labware["Source"]["wells"]["A2"]["volume_ul"] == pytest.approx(40.0)
    assert report.final_labware["Source"]["wells"]["H2"]["volume_ul"] == pytest.approx(40.0)


def test_raw_liha_dispense_adapted() -> None:
    """Raw XML LihaDispense dispenses liquid into the target labware."""
    wt = Worktable(name="raw liha dispense")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Raw")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step("LihaAspirate", _raw_liha_aspirate("Source", 10))
    wt.raw_xml_step("LihaDispense", _raw_liha_dispense("Dest", 10))

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(10.0)


def test_raw_liha_mix_adapted() -> None:
    """Raw XML LihaMix is adapted as validation-only (no state change)."""
    wt = Worktable(name="raw liha mix")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Raw")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step("LihaMix", _raw_liha_mix("Source", 5, cycles=3))

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    # Mix is validation_only; get_tips is fully simulated
    assert report.validation_only_steps >= 1
    assert report.raw_xml_generic_steps == 2


def test_raw_liha_empty_tips_adapted() -> None:
    """Raw XML LihaEmptyTips empties tip contents into destination wells."""
    wt = Worktable(name="raw liha empty")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Raw")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step("LihaAspirate", _raw_liha_aspirate("Source", 10))
    wt.raw_xml_step("LihaEmptyTips", _raw_liha_empty("Dest"))

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    # Dest should have received the emptied tip contents (channel 0 → A1)
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(10.0)


# ── MCA384 raw XML adapter tests ───────────────────────────────────

def test_raw_mca384_get_tips_adapted() -> None:
    """Raw XML Mca384GetTips is adapted and populates 384 tips."""
    wt = Worktable(name="raw mca384 get")
    wt.group("Steps")
    wt.raw_xml_step("Mca384GetTips", _raw_mca384_get_tips())

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.fully_simulated_steps == 1  # TIP_STATE_CHANGE
    assert report.raw_xml_generic_steps == 1


def test_raw_mca384_drop_tips_adapted() -> None:
    """Raw XML Mca384DropTips clears the 384 tips."""
    wt = Worktable(name="raw mca384 drop")
    wt.group("Steps")
    wt.raw_xml_step("Mca384GetTips", _raw_mca384_get_tips())
    wt.raw_xml_step("Mca384DropTips", _raw_mca384_drop_tips())

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.fully_simulated_steps == 2


def test_raw_mca384_move_arm_adapted() -> None:
    """Raw XML Mca384MoveArm is adapted as validation-only."""
    wt = Worktable(name="raw mca384 move")
    wt.group("Steps")
    wt.raw_xml_step("Mca384MoveArm", _raw_mca384_move_arm())

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.validation_only_steps == 1
    assert report.raw_xml_generic_steps == 1


def test_raw_mca384_mix_adapted() -> None:
    """Raw XML Mca384Mix is adapted as validation-only."""
    wt = Worktable(name="raw mca384 mix")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    # MCA384 mix requires adapter mounted (structured step) + tips (raw XML)
    wt.mca96.mount_adapter()
    wt.group("Raw")
    wt.raw_xml_step("Mca384GetTips", _raw_mca384_get_tips())
    wt.raw_xml_step("Mca384Mix", _raw_mca384_mix("Source", 5, cycles=3))

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    # get_tips = fully simulated; mix = validation_only
    assert report.fully_simulated_steps >= 1
    assert report.validation_only_steps >= 1


def test_raw_mca384_empty_tips_adapted() -> None:
    """Raw XML Mca384EmptyTips empties tip contents into destination wells.

    Uses raw XML for get_tips + empty_tips, structured aspirate to load liquid
    (raw MCA384Aspirate adapter does not exist). Asserts actual volume transfer
    from tips → destination wells.
    """
    wt = Worktable(name="raw mca384 empty")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    src.fill_all(Reagent("Buffer"), 50.0)

    # Mount adapter (required by structured AspirateStep)
    wt.mca96.mount_adapter()
    # Raw XML: get tips (populates 384-tip array)
    wt.raw_xml_step("Mca384GetTips", _raw_mca384_get_tips())
    # Structured: aspirate to load liquid into the tips (no raw MCA384Aspirate adapter)
    wt.mca96.aspirate(src, 10.0, liquid_class="Water Free Single")
    # Raw XML: empty tips → should deposit tip contents into Dest wells
    wt.raw_xml_step("Mca384EmptyTips", _raw_mca384_empty("Dest"))

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Source lost 10 µL from A1 (tip[0] pairs with well[0])
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(40.0)
    # Dest gained 10 µL in A1 from emptied tips
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(10.0)
    # Both raw XML steps counted as raw_xml_generic_steps; aspirate is structured
    assert report.raw_xml_generic_steps == 2
    # get_tips=TIP_STATE_CHANGE, aspirate=LIQUID_TRANSFER, empty=LIQUID_TRANSFER
    assert report.fully_simulated_steps >= 3


# ── Unknown command (opaque) control test ───────────────────────────

def test_unknown_raw_command_is_opaque() -> None:
    """An unknown raw XML command is counted as opaque and appears in unsupported IDs."""
    wt = Worktable(name="unknown raw")
    wt.group("Steps")
    wt.raw_xml_step(
        "TotallyMadeUpCommand",
        "<Object><SomeData>value</SomeData></Object>"
    )

    with pytest.raises(SimulationError):
        wt.simulate(SimulationOptions(fail_on_opaque=True))

    report = wt.simulation_report
    assert report is not None
    assert report.opaque_noop_steps == 1
    assert "TotallyMadeUpCommand" in report.unsupported_command_ids
    assert report.raw_xml_generic_steps == 1
    # Opaque events should record the command
    assert len(report.opaque_events) == 1
    assert report.opaque_events[0]["command_id"] == "TotallyMadeUpCommand"


def test_known_commands_not_in_unsupported() -> None:
    """All adapted raw XML commands should NOT appear in unsupported_command_ids."""
    wt = Worktable(name="all known")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    # MCA384 mix requires adapter mounted (structured step)
    wt.mca96.mount_adapter()

    wt.group("Raw")
    # LiHa family — all adapted
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step("LihaAspirate", _raw_liha_aspirate("Source", 5))
    wt.raw_xml_step("LihaDispense", _raw_liha_dispense("Source", 5))
    wt.raw_xml_step("LihaMix", _raw_liha_mix("Source", 3, cycles=2))
    wt.raw_xml_step("LihaEmptyTips", _raw_liha_empty("Source"))
    wt.raw_xml_step("LihaDropTips", '<Object><LabwareName>Trash</LabwareName></Object>')

    # MCA384 family — all adapted
    wt.raw_xml_step("Mca384GetTips", _raw_mca384_get_tips())
    wt.raw_xml_step("Mca384MoveArm", _raw_mca384_move_arm())
    wt.raw_xml_step("Mca384Mix", _raw_mca384_mix("Source", 3, cycles=2))
    wt.raw_xml_step("Mca384EmptyTips", _raw_mca384_empty("Source"))
    wt.raw_xml_step("Mca384DropTips", _raw_mca384_drop_tips())

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    # No unsupported commands — all were adapted successfully
    assert report.unsupported_command_ids == {}
    assert report.opaque_noop_steps == 0
    # 6 LiHa + 5 MCA384 raw XML steps (mount_adapter is structured, not raw)
    assert report.raw_xml_generic_steps == 11

