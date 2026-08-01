"""Category override file loading and application."""

from __future__ import annotations

from pathlib import Path

import pytest

from fluentcoder.catalog.catalog import resolve_by_name
from fluentcoder.catalog.category_overrides import (
    clear_category_overrides_cache,
    load_category_overrides,
)
from fluentcoder.catalog.indexer import build_index
from fluentcoder.catalog.inference import infer_category
from fluentcoder.catalog.xcmp import XcmpArrangement, XcmpComponent, XcmpPipettable

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OVERRIDES_FILE = FIXTURES / "category_overrides.toml"


@pytest.fixture(autouse=True)
def _reset_override_cache():
    clear_category_overrides_cache()
    yield
    clear_category_overrides_cache()


def test_load_category_overrides_from_fixture() -> None:
    overrides = load_category_overrides(OVERRIDES_FILE)
    assert overrides["custom mislabeled runner"] == "tube_rack"
    assert overrides["custom deck segment"] == "fixed_deck"


def test_infer_category_applies_file_override_after_rules(monkeypatch) -> None:
    monkeypatch.setenv("FLUENTCODER_CATEGORY_OVERRIDES", str(OVERRIDES_FILE))
    comp = XcmpComponent(
        guid="runner-guid",
        name="Custom Mislabeled Runner",
        file_path=Path("runner.xcmp"),
        functional_group="Carrier.Runner",
    )
    assert infer_category(comp) == "tube_rack"


def test_infer_category_file_override_can_replace_inferred_category(
    tmp_path: Path, monkeypatch
) -> None:
    overrides = tmp_path / "overrides.toml"
    overrides.write_text('"96 Well Plate" = "fixed_deck"\n', encoding="utf-8")
    monkeypatch.setenv("FLUENTCODER_CATEGORY_OVERRIDES", str(overrides))
    comp = XcmpComponent(
        guid="plate-guid",
        name="96 Well Plate",
        file_path=Path("plate.xcmp"),
        functional_group="Labware.Microplate",
        pipettable=XcmpPipettable(
            x_wells=12,
            y_wells=8,
            x_spacing_mm=9.0,
            y_spacing_mm=9.0,
            first_well_mm=(0.0, 0.0, 0.0),
        ),
    )
    assert infer_category(comp) == "fixed_deck"


def test_build_index_and_resolve_apply_category_override(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "install"
    components = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites = install / "SystemSpecific" / "Worktable" / "Sites"
    components.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    sites.mkdir(parents=True)
    (components / "runner.xcmp").write_text("", encoding="utf-8")
    db_path = tmp_path / "index.db"

    monkeypatch.setenv("FLUENTCODER_CATEGORY_OVERRIDES", str(OVERRIDES_FILE))

    def fake_load_xcmp(path: Path) -> XcmpComponent:
        return XcmpComponent(
            guid="runner-guid",
            name="Custom Mislabeled Runner",
            file_path=path,
            functional_group="Carrier.Runner",
            arrangement=_arrangement(),
        )

    monkeypatch.setattr("fluentcoder.catalog.indexer.load_xcmp", fake_load_xcmp)
    counts = build_index(install_path=install, db_path=db_path)
    assert counts["components"] == 1
    assert counts["tube_rack"] == 1

    entry = resolve_by_name("Custom Mislabeled Runner", db_path=db_path)
    assert entry is not None
    assert entry.category == "tube_rack"


def test_resolve_by_name_applies_override_without_reindex(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "install"
    components = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites = install / "SystemSpecific" / "Worktable" / "Sites"
    components.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    sites.mkdir(parents=True)
    (components / "runner.xcmp").write_text("", encoding="utf-8")
    db_path = tmp_path / "index.db"

    def fake_load_xcmp(path: Path) -> XcmpComponent:
        return XcmpComponent(
            guid="runner-guid",
            name="Custom Mislabeled Runner",
            file_path=path,
            functional_group="Carrier.Runner",
            arrangement=_arrangement(),
        )

    monkeypatch.setattr("fluentcoder.catalog.indexer.load_xcmp", fake_load_xcmp)
    build_index(install_path=install, db_path=db_path)

    before = resolve_by_name("Custom Mislabeled Runner", db_path=db_path)
    assert before is not None
    assert before.category == "fixed_deck"

    monkeypatch.setenv("FLUENTCODER_CATEGORY_OVERRIDES", str(OVERRIDES_FILE))
    after = resolve_by_name("Custom Mislabeled Runner", db_path=db_path)
    assert after is not None
    assert after.category == "tube_rack"


def test_load_category_overrides_rejects_unknown_category(tmp_path: Path) -> None:
    bad = tmp_path / "bad_overrides.toml"
    bad.write_text('"Bad Name" = "not_a_category"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown category"):
        load_category_overrides(bad)


def _arrangement() -> XcmpArrangement:
    return XcmpArrangement(
        sites_in_x=1,
        sites_in_y=1,
        sites_in_z=1,
        site_spacing_mm=(0.0, 0.0, 0.0),
        position_in_parent_mm=(0.0, 0.0, 0.0),
    )

