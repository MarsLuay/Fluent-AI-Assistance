from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import FCA1000Box, InvalidSlotError, MagnetRack, Plate96, Reagent, SimulationError, Worktable  # noqa: E402
from fluentcoder.expressions import parse_expression  # noqa: E402
from fluentcoder.simulator.walk import Simulator  # noqa: E402


def test_simulator_evaluates_ceil_function_expression() -> None:
    wt = Worktable(name="ceil expression")
    wt.set_sim_value("SampleSourceVolume", 15000)
    wt.set_sim_value("VolTransferMax", 3000)
    wt.group("Steps")
    wt.set_variable(
        "NumCycles",
        parse_expression("Ceil((SampleSourceVolume / VolTransferMax))"),
    )
    wt.set_variable(
        "LowerNumCycles",
        parse_expression("ceil((SampleSourceVolume / VolTransferMax))"),
    )

    wt.simulate()

    assert wt.sim_values["NumCycles"] == 5
    assert wt.sim_values["LowerNumCycles"] == 5


def test_simulator_evaluates_fluentcontrol_exponentiation() -> None:
    wt = Worktable(name="exponent expression")
    wt.set_sim_value("tipselection", 0)
    wt.set_sim_value("loopnum", 2)
    wt.group("Steps")
    wt.set_variable("tipselection", parse_expression("tipselection+2^(loopnum-1)"))

    wt.simulate()

    assert wt.sim_values["tipselection"] == 2


def test_simulator_evaluates_lazy_fluentcontrol_if_expression() -> None:
    wt = Worktable(name="if expression")
    wt.set_sim_value("LoopSampleAdd", 3)
    wt.group("Steps")
    wt.set_variable(
        "TubeIndex",
        parse_expression("if((LoopSampleAdd <= 2), 1, if((LoopSampleAdd <= 4), 2, missing))"),
    )

    wt.simulate()

    assert wt.sim_values["TubeIndex"] == 2


def test_simulator_resolves_cover_site_functions_from_simulated_labware() -> None:
    wt = Worktable(name="cover site functions")
    wt.group("Setup")
    wt.place(Plate96("ParkAdapter", catalog="96 Well Flat"), "Nest", 17)
    wt.place(
        Plate96("ElutionRack", catalog="96 Well Flat"),
        'GetCoverSiteName("ParkAdapter")',
        parse_expression('GetCoverSiteIndex("ParkAdapter")'),
        allow_occupied=True,
    )

    wt.simulate()

    assert wt.simulation_report is not None
    assert wt.simulation_report.final_labware["ElutionRack"]["slot"] == ["Nest", 17]


def test_dynamic_place_position_defers_workspace_slot_validation() -> None:
    """Runtime expressions cannot be proved against static workspace slots."""
    wt = Worktable(name="dynamic position")
    wt.valid_slots = {("Nest", 9)}

    placed = wt.place(
        Plate96("Dynamic", catalog="96 Well Flat"),
        "Nest",
        parse_expression("(8 + platecount)"),
    )

    assert placed.slot == ("Nest", "(8 + platecount)")
    with pytest.raises(InvalidSlotError, match="not on workspace"):
        wt.place(Plate96("Invalid", catalog="96 Well Flat"), "Nest", 10)


def test_workspace_seed_is_simulation_only_and_preserves_initial_labware() -> None:
    wt = Worktable(name="seeded workspace")
    wt.seed_simulation_labware(Plate96("OperatorLoaded", catalog="96 Well Flat"), "Nest", 1)
    wt.group("Script")
    wt.add_comment("Use existing labware")

    protocol = wt.to_protocol()
    assert not any(step.step_type == "add_labware" for step in protocol.groups[0].steps)

    wt.simulate()
    assert wt.simulation_report is not None
    assert "OperatorLoaded" in wt.simulation_report.final_labware


def test_raw_liha_with_unresolved_source_workspace_labware_is_opaque_not_fabricated() -> None:
    wt = Worktable(name="unresolved source workspace")
    wt._simulation_unresolved_seed_labels.add("TubeRunner[001]")
    wt.group("Script")
    wt.raw_xml_step(
        "LihaAspirate",
        "<Object><LihaAspirate><LabwareName>TubeRunner[001]</LabwareName>"
        "<Volumes><string>10</string></Volumes></LihaAspirate></Object>",
    )

    wt.simulate()

    assert wt.simulation_report is not None
    [coverage] = wt.simulation_report.steps
    assert coverage.effect.value == "opaque"
    assert "without local catalog geometry" in coverage.message


def test_runtime_bracket_labels_resolve_against_sim_values() -> None:
    wt = Worktable(name="dynamic source labels")
    wt.set_sim_value("platecount", 1)
    wt.set_sim_value("filtercount", 1)
    source = Plate96("FilterDWP[platecount]", catalog="96 Well Flat")
    sim = Simulator(wt)
    sim._twin[source.label] = source

    assert sim._require_labware("FilterDWP[filtercount]", "LiHa dispense") is source


def test_loop_iterator_expands_dynamic_labware_labels_and_positions() -> None:
    wt = Worktable(name="dynamic loop labels")
    wt.set_sim_value("platecount", 2)
    wt.group("Setup")
    with wt.loop(times="platecount", name="Load plates"):
        wt.place(
            Plate96("Plate[platecount]", catalog="96 Well Flat"),
            "Nest",
            parse_expression("platecount"),
        )

    wt.simulate()

    assert wt.simulation_report is not None
    assert set(wt.simulation_report.final_labware) == {"Plate[1]", "Plate[2]"}
    assert wt.simulation_report.final_labware["Plate[1]"]["slot"] == ["Nest", 1]
    assert wt.simulation_report.final_labware["Plate[2]"]["slot"] == ["Nest", 2]


def test_raw_liha_with_unresolvable_runtime_label_is_opaque() -> None:
    wt = Worktable(name="unresolvable dynamic source label")
    wt.group("Script")
    wt.raw_xml_step(
        "LihaAspirate",
        "<Object><LihaAspirate><LabwareName>FilterDWP[filtercount]</LabwareName>"
        "<Volumes><string>10</string></Volumes></LihaAspirate></Object>",
    )

    wt.simulate()

    assert wt.simulation_report is not None
    [coverage] = wt.simulation_report.steps
    assert coverage.effect.value == "opaque"
    assert "not resolvable from the simulated deck" in coverage.message


def test_raw_liha_after_opaque_aspirate_stays_opaque_not_tip_state_failure() -> None:
    wt = Worktable(name="opaque raw LiHa chain")
    wt._simulation_unresolved_seed_labels.add("TubeRunner[001]")
    wt.group("Setup")
    wt.place(Plate96("Destination", catalog="96 Well Flat"), "Nest", 1)
    wt.group("Raw commands")
    wt.raw_xml_step(
        "LihaAspirate",
        "<Object><LihaAspirate><LabwareName>TubeRunner[001]</LabwareName>"
        "<Volumes><string>10</string></Volumes></LihaAspirate></Object>",
    )
    wt.raw_xml_step(
        "LihaDispense",
        "<Object><LihaDispense><LabwareName>Destination</LabwareName>"
        "<Volumes><string>10</string></Volumes></LihaDispense></Object>",
    )

    wt.simulate()

    assert wt.simulation_report is not None
    raw_steps = [step for step in wt.simulation_report.steps if step.raw_xml]
    assert [step.effect.value for step in raw_steps] == ["opaque", "opaque"]
    assert "follows an opaque command" in raw_steps[1].message


def test_authored_protocol_gets_coverage_report() -> None:
    wt = Worktable(name="coverage")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Water"), 20.0)
    wt.wait(1)

    wt.simulate()

    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == len(wt.snapshots)
    assert report.fully_simulated_steps == 1
    assert report.validation_only_steps == 1
    assert report.opaque_noop_steps == 0
    assert report.modeled_coverage == pytest.approx(1.0)
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(20.0)
    assert wt.snapshots[-1].warnings == []


def test_unknown_generic_step_is_reported_and_can_fail() -> None:
    wt = Worktable(name="opaque")
    wt.group("Steps")
    wt.generic_step("UnknownProductionCommand")

    with pytest.raises(SimulationError):
        wt.simulate(fail_on_opaque=True)

    report = wt.simulation_report
    assert report is not None
    assert report.opaque_noop_steps == 1
    assert report.unsupported_command_ids == {"UnknownProductionCommand": 1}
    assert report.opaque_events[0]["command_id"] == "UnknownProductionCommand"


def test_min_coverage_can_fail() -> None:
    wt = Worktable(name="low coverage")
    wt.group("Steps")
    wt.wait(1)
    wt.generic_step("UnknownProductionCommand")

    with pytest.raises(SimulationError):
        wt.simulate(min_coverage=0.75)

    assert wt.simulation_report is not None
    assert wt.simulation_report.modeled_coverage == pytest.approx(0.5)


def test_raw_known_liha_command_is_modeled_once_and_changes_liquid_state() -> None:
    wt = Worktable(name="raw liha")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Raw")
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step(
        "LihaAspirate",
        """
        <Object>
          <LabwareName>Source</LabwareName>
          <Volumes><Object><string>10</string></Object></Volumes>
          <LiquidClassName>Water Free Single</LiquidClassName>
        </Object>
        """,
    )

    wt.simulate()

    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == 3
    assert report.raw_xml_generic_steps == 2
    assert report.unsupported_command_ids == {}
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(40.0)
    assert report.final_liha_tips[0]["volume_ul"] == pytest.approx(10.0)
    assert wt.snapshots[-1].liha_tips[0].volume_ul == pytest.approx(10.0)


# ── Chunk 3: Report arithmetic and semantics ───────────────────────

def test_report_arithmetic_fully_modeled() -> None:
    """Fully modeled protocol: all steps are fully simulated or validation-only."""
    wt = Worktable(name="fully modeled")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Water"), 50.0)
    tip_box = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 2)

    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 10.0, liquid_class="Water Free Single")
    head.return_tips()

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Arithmetic invariants
    assert report.total_executed_steps == len(report.steps)
    modeled = report.fully_simulated_steps + report.validation_only_steps
    assert report.modeled_steps == modeled
    assert report.modeled_coverage == pytest.approx(modeled / report.total_executed_steps)
    # No opaque steps in a fully modeled protocol
    assert report.opaque_noop_steps == 0
    assert len(report.opaque_events) == 0
    assert report.unsupported_command_ids == {}


def test_report_arithmetic_validation_only() -> None:
    """Protocol with only validation-only steps (waits, comments)."""
    wt = Worktable(name="validation only")
    wt.group("Steps")
    wt.wait(1)
    wt.add_comment("hello")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    assert report.total_executed_steps == 2
    assert report.fully_simulated_steps == 0
    assert report.validation_only_steps == 2
    assert report.modeled_coverage == pytest.approx(1.0)  # validation-only counts as modeled
    assert report.opaque_noop_steps == 0


def test_report_arithmetic_opaque() -> None:
    """Protocol with only opaque steps."""
    wt = Worktable(name="opaque")
    wt.group("Steps")
    wt.generic_step("UnknownCmd1")
    wt.generic_step("UnknownCmd2")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    assert report.total_executed_steps == 2
    assert report.fully_simulated_steps == 0
    assert report.validation_only_steps == 0
    assert report.opaque_noop_steps == 2
    assert len(report.opaque_events) == 2
    assert report.modeled_coverage == pytest.approx(0.0)
    assert set(report.unsupported_command_ids.keys()) == {"UnknownCmd1", "UnknownCmd2"}


def test_report_arithmetic_mixed_protocol() -> None:
    """Mixed protocol: modeled + validation-only + opaque steps."""
    wt = Worktable(name="mixed")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)

    # fully simulated (AddLabwareStep → LABWARE_MOVEMENT)
    # validation-only (WaitStep)
    wt.wait(1)
    # opaque (GenericStep with unknown command)
    wt.generic_step("UnknownCmd")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    assert report.total_executed_steps == 3
    assert report.fully_simulated_steps == 1  # AddLabwareStep
    assert report.validation_only_steps == 1  # WaitStep
    assert report.opaque_noop_steps == 1      # GenericStep(unknown)
    modeled = report.fully_simulated_steps + report.validation_only_steps
    assert report.modeled_coverage == pytest.approx(modeled / 3.0)
    assert len(report.opaque_events) == report.opaque_noop_steps
    assert "UnknownCmd" in report.unsupported_command_ids


def test_raw_xml_adapted_still_counts_as_raw() -> None:
    """Raw XML steps that are adapted should still count as raw_xml_generic_steps."""
    wt = Worktable(name="raw adapted")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    # These raw XML steps are adapted to structured LiHa steps internally,
    # but they should still be counted as raw_xml_generic_steps.
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step(
        "LihaAspirate",
        """
        <Object>
          <LabwareName>Source</LabwareName>
          <Volumes><Object><string>5</string></Object></Volumes>
          <LiquidClassName>Water Free Single</LiquidClassName>
        </Object>
        """,
    )

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Both raw XML steps are adapted (not opaque), but still counted as raw_xml
    assert report.raw_xml_generic_steps == 2
    # They should be fully simulated (TIP_STATE_CHANGE + LIQUID_TRANSFER)
    assert report.fully_simulated_steps >= 2
    # No unsupported commands since they were adapted successfully
    assert report.unsupported_command_ids == {}


def test_unsupported_command_ids_only_opaque() -> None:
    """unsupported_command_ids should only contain truly opaque commands."""
    wt = Worktable(name="mixed supported")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Steps")
    # Known command adapted from raw XML → not unsupported
    wt.raw_xml_step("LihaGetTips", "<Object><LihaPickUpScriptCommandDataV1 /></Object>")
    wt.raw_xml_step(
        "LihaAspirate",
        """
        <Object>
          <LabwareName>Source</LabwareName>
          <Volumes><Object><string>5</string></Object></Volumes>
          <LiquidClassName>Water Free Single</LiquidClassName>
        </Object>
        """,
    )
    # Unknown command → opaque
    wt.generic_step("TotallyUnknownCmd")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None

    # Only the unknown command should appear in unsupported_command_ids
    assert "LihaAspirate" not in report.unsupported_command_ids
    assert "TotallyUnknownCmd" in report.unsupported_command_ids


def test_strict_simulation_requires_bound_workspace_and_preserves_report() -> None:
    wt = Worktable(name="strict workspace binding")
    wt.group("Setup")
    wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)

    with pytest.raises(SimulationError, match="not bound to a specific FluentControl workspace"):
        wt.simulate(strict=True)

    report = wt.simulation_report
    assert report is not None
    assert report.status == "failed"
    assert report.total_executed_steps == 0
    assert report.failure is not None
    assert report.failure.category == "workspace_binding"
    assert report.failure.step_index is None


def test_strict_simulation_rejects_invalid_workspace_slot_with_partial_state() -> None:
    wt = Worktable(name="strict bad slot")
    wt.workspace_name = "FakeWorkspace"
    wt.workspace_guid = "11111111-1111-1111-1111-111111111111"
    wt.valid_slots = {("Site", 1)}

    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Site", 1)
    wt.group("Move")
    wt.set_location(src, "Site", 2)

    with pytest.raises(SimulationError):
        wt.simulate(strict=True)

    report = wt.simulation_report
    assert report is not None
    assert report.status == "failed"
    assert report.total_executed_steps == 1
    assert report.failure is not None
    assert report.failure.category == "workspace_slot"
    assert report.failure.command_id == "set_location"
    assert report.final_labware["Source"]["slot"] == ["Site", 1]


def test_failed_report_to_dict_includes_failure_and_effect_counts() -> None:
    wt = Worktable(name="opaque failure report")
    wt.group("Steps")
    wt.generic_step("UnknownProductionCommand")

    with pytest.raises(SimulationError):
        wt.simulate(fail_on_opaque=True)

    report = wt.simulation_report
    assert report is not None
    payload = report.to_dict()
    assert payload["status"] == "failed"
    assert payload["failure"]["category"] == "opaque_policy"
    assert payload["failure"]["exception_type"] == "SimulationError"
    assert payload["effect_counts"]["opaque"] == 1

