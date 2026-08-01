from __future__ import annotations

import sqlite3
from pathlib import Path

from fluentcoder.catalog.catalog import find_components_by_metadata, open_index, resolve_by_name
from fluentcoder.catalog.indexer import build_index
from fluentcoder.catalog.inference import component_taxonomy, infer_category
from fluentcoder.catalog.xcmp import XcmpArrangement, XcmpComponent, XcmpPipettable


def test_existing_component_schema_migrates_taxonomy_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "old-index.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE components (
            guid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            file_path TEXT NOT NULL,
            grid_x INTEGER,
            grid_y INTEGER,
            dim_x_mm REAL,
            dim_y_mm REAL,
            dim_z_mm REAL,
            site_count INTEGER
        );
        INSERT INTO components
            (guid, name, category, file_path, grid_x, grid_y, dim_x_mm, dim_y_mm, dim_z_mm, site_count)
        VALUES
            ('guid-1', 'Old Runner', 'fixed_deck', 'runner.xcmp', 1, 1, 1.0, 2.0, 3.0, 1);
        """
    )
    conn.close()

    with open_index(db_path) as migrated:
        columns = {row["name"] for row in migrated.execute("PRAGMA table_info(components)").fetchall()}

    assert {"functional_group", "component_kind", "component_subtype"}.issubset(columns)
    entry = resolve_by_name("Old Runner", db_path=db_path)
    assert entry is not None
    assert entry.functional_group is None
    assert entry.component_kind == "unknown"
    assert entry.component_subtype is None


def test_component_taxonomy_normalizes_functional_groups() -> None:
    assert component_taxonomy("Carrier.Runner") == ("Carrier.Runner", "carrier", "runner")
    assert component_taxonomy("Carrier.Deck Segment") == ("Carrier.Deck Segment", "carrier", "deck_segment")
    assert component_taxonomy("Labware.Microplate") == ("Labware.Microplate", "labware", "microplate")
    assert component_taxonomy("Tool.MCA96 Tipblocks") == ("Tool.MCA96 Tipblocks", "unknown", "mca96_tipblocks")


def test_build_index_persists_functional_group_taxonomy(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "install"
    components = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites = install / "SystemSpecific" / "Worktable" / "Sites"
    components.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    sites.mkdir(parents=True)
    (components / "runner.xcmp").write_text("", encoding="utf-8")
    (components / "nest.xcmp").write_text("", encoding="utf-8")
    (components / "plate.xcmp").write_text("", encoding="utf-8")
    (components / "alpaqua.xcmp").write_text("", encoding="utf-8")
    db_path = tmp_path / "index.db"

    def fake_load_xcmp(path: Path) -> XcmpComponent:
        name_by_stem = {
            "runner": ("runner-guid", "Runner", "Carrier.Runner", None, _arrangement()),
            "nest": ("nest-guid", "Nest", "Carrier.Nest", None, _arrangement()),
            "plate": ("plate-guid", "96 Well Plate", "Labware.Microplate", _pipettable(), None),
            "alpaqua": ("alpaqua-guid", "LV_Alpaqua_A000350", "Carrier.Nest", None, _arrangement()),
        }
        guid, name, functional_group, pipettable, arrangement = name_by_stem[path.stem]
        return XcmpComponent(
            guid=guid,
            name=name,
            file_path=path,
            dim_mm=(127.0, 86.0, 15.0),
            functional_group=functional_group,
            arrangement=arrangement,
            pipettable=pipettable,
        )

    monkeypatch.setattr("fluentcoder.catalog.indexer.load_xcmp", fake_load_xcmp)
    counts = build_index(install_path=install, db_path=db_path)
    assert counts["components"] == 4

    runner = resolve_by_name("Runner", db_path=db_path)
    nest = resolve_by_name("Nest", db_path=db_path)
    plate = resolve_by_name("96 Well Plate", db_path=db_path)
    alpaqua = resolve_by_name("LV_Alpaqua_A000350", db_path=db_path)

    assert runner is not None
    assert runner.category == "fixed_deck"
    assert runner.functional_group == "Carrier.Runner"
    assert runner.component_kind == "carrier"
    assert runner.component_subtype == "runner"

    assert nest is not None
    assert nest.category == "fixed_deck"
    assert nest.component_kind == "carrier"
    assert nest.component_subtype == "nest"

    assert plate is not None
    assert plate.category == "plate"
    assert plate.component_kind == "labware"
    assert plate.component_subtype == "microplate"

    assert alpaqua is not None
    assert alpaqua.category == "magnet_rack"
    assert alpaqua.component_kind == "carrier"
    assert alpaqua.component_subtype == "nest"

    carriers = find_components_by_metadata("", component_kind="carrier", db_path=db_path)
    assert {entry.name for entry in carriers} == {"Runner", "Nest", "LV_Alpaqua_A000350"}


def test_infer_category_preserves_carrier_magnet_nest_as_fixed_deck() -> None:
    comp = XcmpComponent(
        guid="nest-guid",
        name="Landscape Nest Magnet Teleshake Segment",
        file_path=Path("nest.xcmp"),
        functional_group="Carrier.Nest",
    )
    assert infer_category(comp) == "fixed_deck"


def _arrangement() -> XcmpArrangement:
    return XcmpArrangement(
        sites_in_x=1,
        sites_in_y=1,
        sites_in_z=1,
        site_spacing_mm=(0.0, 0.0, 0.0),
        position_in_parent_mm=(0.0, 0.0, 0.0),
    )


def _pipettable() -> XcmpPipettable:
    return XcmpPipettable(
        x_wells=12,
        y_wells=8,
        x_spacing_mm=9.0,
        y_spacing_mm=9.0,
        first_well_mm=(0.0, 0.0, 0.0),
    )

