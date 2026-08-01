"""Reservoir-shaped labware: troughs and waste pools.

A trough is modelled as a single 'A1' well so the layered/volume bookkeeping
works the same way as for plates and the address matches the rest of the
labware family. The catalog .xcmp may also have a multi-cell arrangement
(e.g. trough + cap segments) — for v1.1 we collapse to one pool; multi-cavity
troughs are a v2 refinement.
"""

from __future__ import annotations

from .base import Labware, Well


class Trough(Labware):
    """Generic single-pool reservoir."""
    category = "trough"
    taxonomic_grid = (0, 0)
    offline_max_well_volume_ul = 25_000.0
    pool_address = "A1"

    def _post_populate(self, *, catalog_entry, comp, max_well_volume_ul) -> None:
        # If the catalog/.xcmp populated multi-well pipettable wells, replace
        # them with a single pool whose max_volume is the sum.
        total_max = sum(w.max_volume_ul for w in self.wells.values()) if self.wells else 0.0
        max_vol = (
            max_well_volume_ul
            if max_well_volume_ul is not None
            else (total_max if total_max > 0 else self.offline_max_well_volume_ul)
        )
        self.wells = {self.pool_address: Well(address=self.pool_address, max_volume_ul=max_vol)}

    @property
    def pool(self) -> Well:
        return self.wells[self.pool_address]


class Trough25mL(Trough):
    offline_max_well_volume_ul = 25_000.0


class Trough100mL(Trough):
    offline_max_well_volume_ul = 100_000.0


class Waste(Trough):
    """A waste reservoir behaves like a trough for state-tracking purposes."""
    offline_max_well_volume_ul = 300_000.0
