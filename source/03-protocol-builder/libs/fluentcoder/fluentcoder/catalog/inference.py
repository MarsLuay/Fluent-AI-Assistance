"""Category inference for FluentControl `.xcmp` components.

The FC install records each component's functional group as a structured
field — e.g. `Labware.Microplate`, `Carrier.Grid Segment`, `Carrier.Hotel`.
That's our primary signal. Substring rules over the component name override
the functional group for the few cases where the name is more specific (a
'Magnet Teleshake Segment' is a `Carrier.Deck Segment` but behaves as a
magnet rack), and serve as a fallback when functional group is missing.

Categories fluentcoder cares about (one Python class per category):

- `plate`        — flat plate (microplate / deep-well / 384)
- `trough`       — single-pool reservoir
- `tip_box`      — DiTi tip box (MCA / FCA)
- `magnet_rack`  — labware that pins beads when stacked under a plate
- `tube_rack`    — discrete tube positions
- `wash_station` — tip wash station
- `waste_chute`  — empty-tip / waste destination
- `hotel`        — multi-z plate storage
- `adapter`      — head accessory (EVA, MCA384 adapter)
- `fixed_deck`   — anything else (deck segments, devices, base units, …)
"""

from __future__ import annotations

import re
from typing import Iterable

from .xcmp import XcmpComponent


# ── Public API ─────────────────────────────────────────────────────


CATEGORIES: tuple[str, ...] = (
    "plate", "trough", "tip_box", "magnet_rack", "tube_rack",
    "wash_station", "waste_chute", "hotel", "adapter", "fixed_deck",
)


def infer_category(comp: XcmpComponent) -> str:
    """Return one of the `CATEGORIES` strings for a parsed XCMP component.

    Built-in exact labware overrides win first; carrier functional groups then
    keep nests/segments as deck infrastructure before substring fallbacks
    classify names like magnet or adapter. Optional ``category_overrides.toml``
    entries apply after those rules. Default is ``fixed_deck`` so unrecognized
    items still load.
    """
    name = comp.name or ""
    fg = (comp.functional_group or "").strip()

    builtin = _lookup_builtin_category_override(name)
    if builtin is not None:
        return builtin

    category = _infer_category_from_rules(comp, name, fg)
    from .category_overrides import get_category_override

    override = get_category_override(name)
    if override is not None:
        return override
    return category


def _infer_category_from_rules(comp: XcmpComponent, name: str, fg: str) -> str:
    # ── 1. Name overrides for carrier-mounted resources that are still
    # protocol-meaningful labware/accessories.
    if _matches(name, _ADAPTER_PATTERNS) and not _matches(name, _NEGATIVE_ADAPTER_PATTERNS):
        if comp.dim_mm and comp.dim_mm[2] < 30.0:
            return "adapter"
    if _matches(name, _WASTE_PATTERNS):
        return "waste_chute"
    if _matches(name, _WASH_PATTERNS):
        return "wash_station"
    if _matches(name, _TUBE_RACK_PATTERNS):
        return "tube_rack"

    # ── 2. Carrier infrastructure beats generic substring names.
    # A "Landscape Nest Magnet Teleshake Segment" contains "Magnet", but it is
    # still a nest/segment mounted on the deck, not a pipettable/stacked
    # magnet-plate resource for a protocol object draft.
    # Exact overrides above deliberately run before this guard: Alpaqua magnet
    # plates are physical SBS-format labware even when their component metadata
    # looks carrier-like.
    if fg.startswith("Carrier."):
        return _FG_TO_CATEGORY.get(fg, "fixed_deck")

    # ── 3. Name overrides for non-carrier labware ──
    if _matches(name, _MAGNET_PATTERNS):
        return "magnet_rack"

    # ── 4. Primary: functional group ──
    by_fg = _FG_TO_CATEGORY.get(fg)
    if by_fg is not None:
        # Some FGs need name disambiguation.
        if by_fg == "wash_or_waste":
            return "waste_chute" if "waste" in name.lower() else "wash_station"
        return by_fg

    # ── 5. Fallback: substring on name ──
    if _matches(name, _WASTE_PATTERNS):
        return "waste_chute"
    if _matches(name, _WASH_PATTERNS):
        return "wash_station"
    if _matches(name, _HOTEL_PATTERNS):
        return "hotel"
    if _matches(name, _TIP_BOX_PATTERNS) and _has_plate_grid(comp):
        return "tip_box"
    # Tube_rack runs before the structural-carrier exclusion: a "Tube Runner"
    # has addressable tube positions, even though it's named "Runner".
    if _matches(name, _TUBE_RACK_PATTERNS):
        return "tube_rack"
    # A "Runner" / "Downholder" / "Holder" / "Stand" is structural — it carries
    # other labware but isn't pipetted into. Route to fixed_deck so we don't
    # mis-classify e.g. '1x4 100ml Trough Runner' as a trough.
    if _matches(name, _STRUCTURAL_CARRIER_PATTERNS):
        return "fixed_deck"
    if _matches(name, _PLATE_PATTERNS) and (comp.pipettable is not None):
        return "plate"
    if _matches(name, _TROUGH_PATTERNS):
        return "trough"

    return "fixed_deck"


def component_taxonomy(functional_group: str | None) -> tuple[str | None, str, str | None]:
    """Return persisted component taxonomy from an FC functional group.

    The first item is the original FluentControl functional-group string. The
    major kind is intentionally small and stable for protocol decisions.
    """
    fg = functional_group.strip() if functional_group else None
    if not fg:
        return None, "unknown", None
    major, _, subtype = fg.partition(".")
    kind = major.strip().lower()
    if kind not in {"carrier", "labware"}:
        kind = "unknown"
    normalized_subtype = _normalize_taxonomy_token(subtype) if subtype else None
    return fg, kind, normalized_subtype


# ── Functional-group → category map ────────────────────────────────


_FG_TO_CATEGORY: dict[str, str] = {
    # Labware: holds liquid or tips.
    "Labware.Microplate": "plate",
    "Labware.Deep Well": "plate",
    "Labware.MCA96 DiTi": "tip_box",
    "Labware.MCA96 Adapter DiTi": "tip_box",
    "Labware.MCA384 DiTi": "tip_box",
    "Labware.MCA384 Adapter DiTi": "tip_box",
    "Labware.FCA DiTi": "tip_box",
    "Labware.Trough": "trough",
    "Labware.Wash and Waste": "wash_or_waste",  # disambiguated by name
    "Labware.Tube": "tube_rack",                 # single tube treated as 1×1 rack
    "Labware.Miscellaneous": "fixed_deck",       # RoboColumns etc.

    # Carriers: hold labware on the worktable.
    "Carrier.Hotel": "hotel",
    # Carrier.Runner deliberately omitted — name disambiguates: 'Tube Runner' is a
    # tube_rack (substring fallback), 'Trough Runner' is a structural holder
    # (falls through to fixed_deck).
    "Carrier.Nest": "fixed_deck",      # Nests are often holders; magnets caught by name override
    "Carrier.Deck Segment": "fixed_deck",
    "Carrier.Grid Segment": "fixed_deck",
    "Carrier.Base Unit": "fixed_deck",
    "Carrier.Device": "fixed_deck",
    "Carrier.Miscellaneous": "fixed_deck",
}


# ── Substring patterns ─────────────────────────────────────────────


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_MAGNET_PATTERNS = _compile(r"\bmagnet\b", r"magniflex")
_ADAPTER_PATTERNS = _compile(r"\badapter\b", r"\beva\b")
_NEGATIVE_ADAPTER_PATTERNS = _compile(r"adapter\s*nest", r"adapter\s*segment")
_WASTE_PATTERNS = _compile(r"waste\s*chute", r"waste\s*trough")
_WASH_PATTERNS = _compile(r"wash\s*station", r"wash.*cleaner", r"\bwash\b")
_HOTEL_PATTERNS = _compile(r"\bhotel\b", r"\bstack\d", r"passive\s*stack")
_TIP_BOX_PATTERNS = _compile(r"\bbox\b", r"\bditi\b", r"mca96.*ul", r"mca384.*ul", r"fca,?\s*\d+ul", r"filtered", r"nested")
_TUBE_RACK_PATTERNS = _compile(r"eppendorf", r"falcon", r"cryo", r"\btube\b", r"vacutainer")
_PLATE_PATTERNS = _compile(r"\d+\s*well", r"\bpcr\b", r"microplate")
_TROUGH_PATTERNS = _compile(r"\btrough\b", r"\breservoir\b")
_STRUCTURAL_CARRIER_PATTERNS = _compile(r"\brunner\b", r"\bdownholder\b", r"\bstand\b", r"\bholder\b")

_EXACT_CATEGORY_OVERRIDES = {
    "lv_alpaqua_a000350": "magnet_rack",
    "lv_alpaqua_a000350_1": "magnet_rack",
    "lv_alpaqua_384": "magnet_rack",
    "24 magnet plate": "magnet_rack",
}


def _lookup_builtin_category_override(name: str) -> str | None:
    return _EXACT_CATEGORY_OVERRIDES.get(name.strip().lower())


def _matches(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def _normalize_taxonomy_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _has_plate_grid(comp: XcmpComponent) -> bool:
    """Component has an arrangement compatible with a 96- or 384-well grid."""
    arr = comp.arrangement
    if arr is None:
        return False
    return (arr.sites_in_x, arr.sites_in_y) in {
        (12, 8), (8, 12), (24, 16), (16, 24),
    }
