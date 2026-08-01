"""v1.1 acceptance: catalog-driven labware construction."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import Plate96, Trough100mL, MCA100Box, MagnetRack  # noqa: E402
from fluentcoder.catalog.catalog import index_exists  # noqa: E402


@pytest.mark.usefixtures("synthetic_catalog")
def test_plate96_from_catalog_populates_real_facts() -> None:
    src = Plate96("Source", catalog="96 Well Flat")
    assert src.catalog_name == "96 Well Flat"
    assert len(src.wells) == 96
    # Cavity-derived max volume is ~392 µL for a Microplate 96-well.
    assert 380 < src.well("A1").max_volume_ul < 410
    # mm geometry is populated.
    assert src.dim_mm is not None
    assert 100 < src.dim_mm[0] < 130
    # Per-well position is computed from XCMP first-well + spacing.
    a1_pos = src.well("A1").position_mm
    assert a1_pos is not None
    h12_pos = src.well("H12").position_mm
    assert h12_pos is not None
    # H12 is 11 columns over and 7 rows down from A1 — different from A1.
    assert h12_pos != a1_pos


@pytest.mark.usefixtures("synthetic_catalog")
def test_trough_collapses_to_single_pool() -> None:
    trough = Trough100mL("WashBuffer", catalog="100ml Trough 156mm")
    assert len(trough.wells) == 1
    assert trough.pool.max_volume_ul >= 50_000  # roughly 100 mL


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_tipbox_carries_catalog_name() -> None:
    tips = MCA100Box("Tips", catalog="MCA96, 100ul, Box")
    assert tips.catalog_name == "MCA96, 100ul, Box"
    assert tips.is_full is True
    assert tips.capacity_ul == 100.0


@pytest.mark.usefixtures("synthetic_catalog")
def test_unknown_catalog_name_raises() -> None:
    with pytest.raises(ValueError, match="not found in fluentcoder catalog index"):
        Plate96("Bogus", catalog="No Such Plate Ever")


@pytest.mark.usefixtures("synthetic_catalog")
def test_missing_catalog_when_index_present_raises() -> None:
    with pytest.raises(ValueError, match="must pass `catalog="):
        Plate96("Source")


@pytest.mark.usefixtures("synthetic_catalog")
def test_plate96_uses_registered_class_default_catalog() -> None:
    from fluentcoder import Worktable
    from fluentcoder.defaults import set_catalog_defaults

    set_catalog_defaults({Plate96: "96 Well Flat"})
    src = Plate96("Source")
    assert src.catalog_name == "96 Well Flat"
    assert len(src.wells) == 96


@pytest.mark.usefixtures("synthetic_catalog")
def test_place_plate96_without_catalog_after_worktable_default() -> None:
    from fluentcoder import Worktable

    wt = Worktable(name="Default catalog test")
    wt.set_default_catalog(Plate96, "96 Well Flat")
    wt.group("Setup")
    placed = wt.place(Plate96("Source"), "Nest", 1)
    assert placed.catalog_name == "96 Well Flat"
    proto = wt.to_protocol()
    assert proto.groups[0].steps[0].labware_type == "96 Well Flat"


@pytest.mark.usefixtures("synthetic_catalog")
def test_compile_renders_real_catalog_name() -> None:
    """The IR step's labware_type should be the exact catalog name."""
    from fluentcoder import Worktable
    wt = Worktable(name="Catalog test")
    wt.group("Setup")
    wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    proto = wt.to_protocol()
    add_step = proto.groups[0].steps[0]
    assert add_step.labware_type == "96 Well Flat"

