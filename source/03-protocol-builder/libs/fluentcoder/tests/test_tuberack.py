"""TubeRack v2: per-tube fill state and cap-closed simulator rules."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import (  # noqa: E402
    CannotAspirateError,
    FCA1000Box,
    OverdrawError,
    Plate96,
    Reagent,
    TubeRack,
    Worktable,
)
from fluentcoder.catalog import catalog as catalog_module  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_module, "index_exists", lambda: False)


def test_offline_tube_rack_synthesizes_wells() -> None:
    rack = TubeRack("Tubes")
    assert len(rack.wells) == 24
    assert "A1" in rack.wells
    assert rack.wells["A1"].max_volume_ul == pytest.approx(1500.0)


def test_partial_fill_per_tube() -> None:
    rack = TubeRack("Tubes")
    buffer = Reagent("Buffer")
    water = Reagent("Water")
    rack.fill_tube("A1", buffer, 100.0)
    rack.fill_tube("B1", water, 50.0)

    assert rack.well("A1").volume_ul == pytest.approx(100.0)
    assert rack.well("A1").layers[0].reagent is buffer
    assert rack.well("B1").volume_ul == pytest.approx(50.0)
    assert rack.well("C1").is_empty


def test_well_add_layer_partial_fill() -> None:
    """Per-tube fill via well().add_layer() without fill_tube()."""
    rack = TubeRack("Tubes")
    sample = Reagent("Sample")
    rack.well("A1").add_layer(sample, 100.0)

    assert rack.well("A1").volume_ul == pytest.approx(100.0)
    assert rack.well("A1").layers[0].reagent is sample
    assert rack.well("A2").is_empty


def test_liha_aspirates_only_from_filled_tube() -> None:
    """A1 holds 100 uL, A2 empty — LiHa well_offset=0 touches A1 only."""
    wt = Worktable(name="partial tube LiHa")
    rack = wt.place(TubeRack("Source"), "Nest", 1)
    sample = Reagent("Sample")
    rack.well("A1").add_layer(sample, 100.0)
    assert rack.well("A2").is_empty

    wt.group("Pipette")
    wt.liha.get_tips(tip_index=0)
    wt.liha.aspirate(rack, 40.0, liquid_class="Water Free Single", well_offset=0)
    wt.liha.drop_tips("Trash")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    wells = report.final_labware["Source"]["wells"]
    assert wells["A1"]["volume_ul"] == pytest.approx(60.0)
    assert "A2" not in wells


def test_mca_single_tip_aspirates_first_tube_only() -> None:
    """A1 holds 100 uL, A2 empty — one MCA tip aspirates from A1 only."""
    wt = Worktable(name="partial tube MCA")
    rack = wt.place(TubeRack("Source"), "Nest", 1)
    tips = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 3)
    rack.well("A1").add_layer(Reagent("Sample"), 100.0)
    assert rack.well("A2").is_empty

    wt.group("Pipette")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips, tip_count=1)
    wt.mca96.aspirate(rack, 25.0, liquid_class="Water Free Single")
    wt.mca96.return_tips(tips)

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    wells = report.final_labware["Source"]["wells"]
    assert wells["A1"]["volume_ul"] == pytest.approx(75.0)
    assert "A2" not in wells


def test_cap_state_helpers() -> None:
    rack = TubeRack("Tubes")
    rack.set_cap("A1", closed=True)
    rack.set_all_caps(closed=True)
    assert all(w.cap_closed for w in rack.wells.values())
    rack.set_cap("A1", closed=False)
    assert not rack.well("A1").cap_closed
    assert rack.well("B1").cap_closed


def test_body_geometry_reports_cap_closed() -> None:
    rack = TubeRack("Tubes")
    rack.set_cap("A1", closed=True)
    body = rack.body_geometry()
    assert body["tubes"]["A1"]["cap_closed"] is True
    assert "cap_closed" not in body["tubes"]["B1"]


def test_aspirate_capped_tube_raises() -> None:
    wt = Worktable(name="cap aspirate")
    rack = wt.place(TubeRack("Tubes"), "Nest", 1)
    rack.fill_tube("A1", Reagent("Sample"), 200.0)
    rack.set_cap("A1", closed=True)

    wt.group("Pipette")
    wt.liha.get_tips(tip_index=0)
    wt.liha.aspirate(rack, 10.0, liquid_class="Water Free Single", well_offset=0)

    with pytest.raises(CannotAspirateError, match="cap is closed"):
        wt.simulate()


def test_aspirate_uncapped_partial_fill_succeeds() -> None:
    wt = Worktable(name="partial aspirate")
    src = wt.place(TubeRack("Source"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    src.fill_tube("A1", Reagent("Sample"), 80.0)

    wt.group("Pipette")
    wt.liha.get_tips(tip_index=0)
    wt.liha.aspirate(src, 25.0, liquid_class="Water Free Single", well_offset=0)
    wt.liha.dispense(dst, 25.0, liquid_class="Water Free Single")
    wt.liha.drop_tips("Trash")

    wt.simulate()
    report = wt.simulation_report
    assert report is not None
    assert report.final_labware["Source"]["wells"]["A1"]["volume_ul"] == pytest.approx(55.0)
    assert report.final_labware["Dest"]["wells"]["A1"]["volume_ul"] == pytest.approx(25.0)


def test_dispense_into_capped_tube_raises() -> None:
    wt = Worktable(name="cap dispense")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(TubeRack("Dest"), "Nest", 2)
    src.fill_all(Reagent("Buffer"), 50.0)
    dst.set_cap("A1", closed=True)

    wt.group("Pipette")
    wt.liha.get_tips(tip_index=0)
    wt.liha.aspirate(src, 10.0, liquid_class="Water Free Single")
    wt.liha.dispense(dst, 10.0, liquid_class="Water Free Single", well_offset=0)

    with pytest.raises(CannotAspirateError, match="cap is closed"):
        wt.simulate()


def test_dispense_into_uncapped_tube_respects_overflow() -> None:
    wt = Worktable(name="tube overflow")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(TubeRack("Dest"), "Nest", 2)
    src.fill_all(Reagent("Buffer"), 2000.0)
    dst.fill_tube("A1", Reagent("Old"), 1400.0)

    wt.group("Pipette")
    wt.liha.get_tips()
    wt.liha.aspirate(src, 200.0, liquid_class="Water Free Single")
    wt.liha.dispense(dst, 200.0, liquid_class="Water Free Single", well_offset=0)

    with pytest.raises(OverdrawError, match="would overflow"):
        wt.simulate()


def test_mca_aspirate_capped_tube_raises() -> None:
    wt = Worktable(name="mca cap")
    rack = wt.place(TubeRack("Tubes"), "Nest", 1)
    tips = wt.place(FCA1000Box("Tips", catalog="FCA, 1000ul"), "Nest", 3)
    rack.fill_tube("A1", Reagent("Sample"), 500.0)
    rack.set_cap("A1", closed=True)

    wt.group("Pipette")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    wt.mca96.aspirate(rack, 20.0, liquid_class="Water Free Single")

    with pytest.raises(CannotAspirateError, match="cap is closed"):
        wt.simulate()

