"""Tube racks — discrete tube positions (Eppendorf, Falcon, Cryo, …)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .base import Labware, Well, well_grid_addresses

if TYPE_CHECKING:
    from ..reagent import Reagent


class TubeRack(Labware):
    """Tube rack with discrete tube positions.

    v1.1 exposes each tube position as a `Well` so aspirate/dispense semantics
    match plates. Per-position max volume comes from the catalog (.xcmp
    pipettable cavity); offline defaults to 1.5 mL on a 4×6 grid.

    v2 per-tube state (minimal):
    - Independent `Well.layers` per tube for partial fills and reagent identity.
    - `Well.cap_closed` (default ``False``) blocks pipette access when ``True``.
      Use `set_cap()` / `set_all_caps()` at authoring time.
    - `fill_tube()` sets a single tube's fill without touching neighbours.

    Deferred: catalog-driven default cap state, pierce-cap / septum dispense,
    and explicit per-tube reagent labels beyond `Layer.reagent`.
    """

    category = "tube_rack"
    taxonomic_grid = (0, 0)            # discovered from catalog
    offline_taxonomic_grid = (4, 6)    # 24-position rack when catalog unavailable
    offline_max_well_volume_ul = 1500.0
    offline_dim_mm = (127.76, 85.48, 40.0)
    offline_first_well_mm = (12.0, 12.0, 0.0)
    offline_well_spacing_mm = (18.0, 18.0)
    offline_well_shape = "Cylinder"
    offline_well_footprint = "round"
    offline_well_height_mm = 35.0

    def fill_tube(self, address: str, reagent: "Reagent", volume_ul: float) -> None:
        """Set one tube to a partial or full fill."""
        well = self.well(address)
        well.layers = []
        well.add_layer(reagent, volume_ul)

    def set_cap(self, address: str, *, closed: bool) -> None:
        """Set cap state for one tube position."""
        self.well(address).cap_closed = closed

    def set_all_caps(self, *, closed: bool) -> None:
        """Set cap state on every tube position."""
        for well in self.wells.values():
            well.cap_closed = closed

    def _post_populate(self, *, catalog_entry, comp, max_well_volume_ul) -> None:
        if not self.wells:
            self._synthesize_offline_grid(max_well_volume_ul=max_well_volume_ul)

    def _synthesize_offline_grid(self, *, max_well_volume_ul: Optional[float]) -> None:
        rows, cols = self.offline_taxonomic_grid
        max_vol = (
            max_well_volume_ul
            if max_well_volume_ul is not None
            else self.offline_max_well_volume_ul
        )
        well_geometry = self._offline_well_geometry()
        for addr in well_grid_addresses(rows, cols):
            self.wells[addr] = Well(
                address=addr,
                max_volume_ul=max_vol,
                position_mm=self._offline_well_position_mm(addr),
                geometry=dict(well_geometry),
            )

    def _body_shape(self) -> str:
        return "tube" if len(self.wells) == 1 else "tube_rack"

    def _well_body_geometry(self, well: Well) -> dict[str, Any]:
        body = super()._well_body_geometry(well)
        if well.cap_closed:
            body["cap_closed"] = True
        return body

    def body_geometry(self) -> dict[str, Any]:
        body = super().body_geometry()
        tubes: dict[str, dict[str, Any]] = {}
        for address, well in self.wells.items():
            well_body = self._well_body_geometry(well)
            tubes[address] = {
                **well_body,
                "tube_height_mm": well_body.get("height_mm") or self.offline_well_height_mm,
                "tube_footprint": well_body.get("footprint") or "round",
            }
        body["tubes"] = tubes
        body["tube_count"] = len(tubes)
        return body
