"""v1.1 acceptance: category inference rules categorize known names correctly."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.catalog.indexer import install_path_default  # noqa: E402
from fluentcoder.catalog.inference import infer_category  # noqa: E402
from fluentcoder.catalog.xcmp import load_xcmp  # noqa: E402

pytestmark = pytest.mark.fluentcontrol_shell


_INSTALL = install_path_default() / "SystemSpecific" / "Worktable" / "Components"
_SYNTHETIC_INSTALL = (
    Path(__file__).resolve().parent / "fixtures" / "synthetic_catalog" / "install"
)


def _install_present() -> bool:
    install = install_path_default()
    try:
        if install.resolve() == _SYNTHETIC_INSTALL.resolve():
            return True
    except OSError:
        if install == _SYNTHETIC_INSTALL:
            return True
    return _INSTALL.exists()


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
@pytest.mark.parametrize("name,expected", [
    ("96 Well Flat", "plate"),
    ("96 Deep Well 0.5ml", "plate"),
    ("384 Well", "plate"),
    ("MCA96, 100ul, Box", "tip_box"),
    ("MCA96, 50ul, Box_new", "tip_box"),
    ("FCA, 200ul SBS", "tip_box"),
    ("MCA384, 50ul", "tip_box"),
    ("100ml Trough 156mm", "trough"),
    ("25ml_short", "trough"),
    ("24 Magnet Plate", "magnet_rack"),
    ("LV_Alpaqua_A000350", "magnet_rack"),
    ("LV_Alpaqua_A000350_1", "magnet_rack"),
    ("LV_Alpaqua_384", "magnet_rack"),
    ("Landscape Nest Magnet Teleshake Segment", "fixed_deck"),
    ("MCA96 Wash Station", "wash_station"),
    ("Wash Station Cleaner Back Tube Rotator", "wash_station"),
    ("MCA Thru Deck Waste Chute", "waste_chute"),
    ("FCA Thru Deck Waste Chute", "waste_chute"),
    ("15 Microplate Passive Stack", "hotel"),
    ("9 Nest Hotel", "hotel"),
    ("Teleshake Adapter Plate", "adapter"),
    ("1x16 15ml Falcon Tube Runner", "tube_rack"),
    ("3x32 10mm Tube Runner no Tubes", "tube_rack"),
    ("1x4 100ml Trough Runner", "fixed_deck"),
    ("Stacker Right", "fixed_deck"),
    ("Fluent ID Left 5 Grid", "fixed_deck"),
])
def test_inference_categorizes_known_name(name: str, expected: str) -> None:
    matches = [p for p in _INSTALL.glob("*.xcmp") if _xcmp_top_name(p) == name]
    if not matches:
        pytest.skip(f"{name!r} not in this install")
    comp = load_xcmp(matches[0])
    assert infer_category(comp) == expected, (
        f"{name!r}: got {infer_category(comp)}, expected {expected}"
    )


def _xcmp_top_name(path: Path) -> str:
    """Cheap text scan for the top-level <ObjectName>; avoids re-parsing on miss."""
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if "<ObjectName>" in line:
                    s = line.split("<ObjectName>", 1)[1].split("</ObjectName>", 1)[0]
                    return s.strip()
    except Exception:
        pass
    return ""

