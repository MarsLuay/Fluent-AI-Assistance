"""Labware body geometry descriptors for deck renderers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = Path(__file__).resolve().parents[3]

from fluentcoder import (  # noqa: E402
    Adapter,
    FCA1000Box,
    FixedDeck,
    Hotel,
    Plate,
    Plate384,
    Plate96Deep,
    TubeRack,
    WashStation,
    Worktable,
)
from fluentcoder.catalog import catalog as catalog_module  # noqa: E402
from fluentcoder.catalog.xcmp import load_xcmp  # noqa: E402


_READY = PROJECT_ROOT / "ready-to-import"


def _optional_component_xcmp(guid: str) -> Path:
    matches = sorted(
        _READY.glob(f"*/temp_files/extracted/DataStore/SystemSpecific/Worktable/Components/{guid}.xcmp")
    )
    if matches:
        return matches[0]
    return (
        _READY
        / "_missing"
        / "temp_files"
        / "extracted"
        / "DataStore"
        / "SystemSpecific"
        / "Worktable"
        / "Components"
        / f"{guid}.xcmp"
    )


DWP_XCMP = _optional_component_xcmp("f916f948-28f0-47ef-aac3-78cc023712d8")
FALCON_XCMP = _optional_component_xcmp("1b92486f-f46e-4ab4-bcf3-7b951a89f137")


def _entry(name: str, path: Path):
    comp = load_xcmp(path)
    dim = comp.dim_mm or (0.0, 0.0, 0.0)
    return SimpleNamespace(
        name=name,
        file_path=path,
        dim_x_mm=dim[0],
        dim_y_mm=dim[1],
        dim_z_mm=dim[2],
    )


def _patch_catalog(monkeypatch, entries):
    monkeypatch.setattr(catalog_module, "index_exists", lambda: True)
    monkeypatch.setattr(catalog_module, "resolve_by_name", lambda name: entries.get(name))


@pytest.mark.skipif(not DWP_XCMP.exists(), reason="24 DWP XCMP fixture not extracted")
def test_dwp_body_uses_real_spacing_and_rectangular_well_geometry(monkeypatch) -> None:
    _patch_catalog(monkeypatch, {"24 DWP": _entry("24 DWP", DWP_XCMP)})
    comp = load_xcmp(DWP_XCMP)

    plate = Plate("DWP", catalog="24 DWP")
    body = plate.body_geometry()

    assert body["component"]["guid"] == comp.guid
    assert body["component"]["name"] == comp.name
    assert body["component"].get("functional_group") == comp.functional_group
    assert body["component"].get("footprint") == comp.footprint
    assert body["component"].get("renderer") == comp.renderer
    assert body["component"]["mesh_object_names"] == list(comp.mesh_object_names)
    assert body["component"]["sub_component_names"] == list(comp.sub_component_names)
    assert body["body_shape"] == "deep_well_plate"
    assert body["grid"]["columns"] == 6
    assert body["grid"]["rows"] == 4
    assert body["grid"]["x_spacing_mm"] == pytest.approx(18.136)
    assert body["well_geometry"]["footprint"] == "rectangular"
    assert body["wells"]["A1"]["position_mm"] != body["wells"]["D6"]["position_mm"]


@pytest.mark.skipif(not FALCON_XCMP.exists(), reason="Falcon tube XCMP fixture not extracted")
def test_tube_rack_body_preserves_tube_wells_and_height(monkeypatch) -> None:
    _patch_catalog(monkeypatch, {"15ml Falcon": _entry("15ml Falcon", FALCON_XCMP)})
    comp = load_xcmp(FALCON_XCMP)

    rack = TubeRack("Falcon", catalog="15ml Falcon")
    body = rack.body_geometry()

    assert body["component"]["guid"] == comp.guid
    assert body["component"].get("renderer") == comp.renderer
    assert body["body_shape"] == "tube"
    assert body["tube_count"] == 1
    assert body["tubes"]["A1"]["tube_footprint"] == "round"
    assert body["tubes"]["A1"]["tube_height_mm"] > 100


@pytest.mark.skipif(not DWP_XCMP.exists(), reason="24 DWP XCMP fixture not extracted")
def test_carrier_body_preserves_official_arrangement_geometry(monkeypatch) -> None:
    comp = load_xcmp(DWP_XCMP)
    if comp.arrangement is None:
        pytest.skip("DWP fixture does not include arrangement geometry")
    _patch_catalog(monkeypatch, {"24 DWP": _entry("24 DWP", DWP_XCMP)})

    carrier = FixedDeck("DWP carrier", catalog="24 DWP")
    body = carrier.body_geometry()

    assert body["arrangement"]["site_count"] == comp.arrangement.site_count
    assert body["arrangement"]["sites_in_x"] == comp.arrangement.sites_in_x
    assert body["arrangement"]["sites_in_y"] == comp.arrangement.sites_in_y
    assert body["arrangement"]["site_spacing_mm"] == comp.arrangement.site_spacing_mm
    assert body["arrangement"]["position_in_parent_mm"] == comp.arrangement.position_in_parent_mm


def test_tip_box_body_tracks_occupied_and_available_locations(monkeypatch) -> None:
    monkeypatch.setattr(catalog_module, "index_exists", lambda: False)

    tips = FCA1000Box("Tips")
    assert tips.tip_count == 96

    consumed = tips.consume_tips(8)
    body = tips.body_geometry()

    assert len(consumed) == 8
    assert body["tip_state"]["occupied_count"] == 88
    assert body["tip_state"]["used_count"] == 8
    assert all(body["tips"][address]["occupied"] is False for address in consumed)

    tips.return_tips(8)
    assert tips.is_full is True


def test_simulation_report_exposes_partial_tip_occupancy(monkeypatch) -> None:
    monkeypatch.setattr(catalog_module, "index_exists", lambda: False)

    wt = Worktable(name="Tip occupancy")
    wt.group("Setup")
    tips = wt.place(FCA1000Box("Tips"), "Site", 1)
    wt.group("Run")
    wt.liha.get_tips(tips)

    wt.simulate()

    tip_box = wt.simulation_report.state_summary["tip_state"]["tip_boxes"][0]
    assert tip_box["tip_count"] == 96
    assert tip_box["used_tip_count"] == 8
    assert tip_box["available_tip_count"] == 88
    assert sum(1 for tip in tip_box["locations"].values() if tip["used"]) == 8


def test_offline_plate_and_hardware_bodies_are_distinct(monkeypatch) -> None:
    monkeypatch.setattr(catalog_module, "index_exists", lambda: False)

    assert Plate96Deep("DWP").body_geometry()["body_shape"] == "deep_well_plate"
    plate384 = Plate384("Plate384").body_geometry()
    assert plate384["grid"]["rows"] == 16
    assert plate384["grid"]["columns"] == 24
    assert plate384["grid"]["x_spacing_mm"] == 4.5

    carrier = FixedDeck("Carrier")
    carrier.site_offsets_mm = ((0.0, 0.0, 0.0), (110.0, 0.0, 0.0))
    assert carrier.body_geometry()["body_shape"] == "carrier"

    assert FixedDeck("Source Cap Holder").body_geometry()["body_shape"] == "cap_holder"
    assert WashStation("Washer").body_geometry()["body_shape"] == "wash_station"
    assert Hotel("Hotel").body_geometry()["features"][0]["shape"] == "vertical_shelf_stack"
    assert Adapter("EVA").body_geometry()["body_shape"] == "head_adapter"

