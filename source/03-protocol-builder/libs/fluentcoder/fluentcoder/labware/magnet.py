"""Magnet rack labware. Plates stacked above are magnetized."""

from __future__ import annotations

from .base import Labware


class MagnetRack(Labware):
    """Magnet plate / rack.

    Any labware sitting above this in the slot stack has
    `is_magnetized == True`. Catalog-driven; offline default is a synthetic
    placeholder.
    """

    category = "magnet_rack"
    taxonomic_grid = (0, 0)
