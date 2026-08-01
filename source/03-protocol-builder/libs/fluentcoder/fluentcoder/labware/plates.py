"""Plate-shaped labware: 96-well, 96-deep, 384-well.

Behavior is shared (`Plate`); subclasses fix the *taxonomic* grid for
authoring ergonomics and offline-mode synthesis. Per-catalog facts
(exact dimensions, well max-volume from cavity geometry) come from the
SQL index + .xcmp parse on construction.
"""

from __future__ import annotations

from typing import Any

from .base import Labware


class Plate(Labware):
    """Generic plate. Subclass for fixed-grid families."""

    category = "plate"
    taxonomic_grid = (0, 0)
    offline_max_well_volume_ul = 350.0
    offline_dim_mm = (127.76, 85.48, 14.35)
    offline_first_well_mm = (14.38, 11.24, 0.0)
    offline_well_spacing_mm = (9.0, 9.0)
    offline_well_shape = "Cylinder"
    offline_well_footprint = "round"
    offline_well_height_mm = 10.0

    def _body_shape(self) -> str:
        name = f"{self.catalog_name} {self.label}".lower()
        if "filter" in name:
            return "filter_plate"
        if "dwp" in name or "deep" in name or (self.dim_mm is not None and self.dim_mm[2] >= 25.0):
            return "deep_well_plate"
        return "plate"

    def body_geometry(self) -> dict[str, Any]:
        body = super().body_geometry()
        grid = body.get("grid") or {}
        if self._pipettable_geometry:
            body["well_geometry"] = {
                key: value
                for key, value in self._pipettable_geometry.items()
                if key != "grid"
            }
        else:
            body["well_geometry"] = {
                "shape": self.offline_well_shape,
                "footprint": self.offline_well_footprint,
                "height_mm": self.offline_well_height_mm,
            }
        if grid:
            body["well_spacing_mm"] = {
                "x": grid.get("x_spacing_mm"),
                "y": grid.get("y_spacing_mm"),
            }
        return body


class Plate96(Plate):
    """Standard 96-well plate (8 rows × 12 cols)."""
    taxonomic_grid = (8, 12)
    offline_max_well_volume_ul = 392.0


class Plate96Deep(Plate):
    """96-deep-well plate."""
    taxonomic_grid = (8, 12)
    offline_max_well_volume_ul = 1000.0
    offline_dim_mm = (127.76, 85.48, 44.0)
    offline_well_height_mm = 35.0


class Plate384(Plate):
    """Standard 384-well plate (16 rows × 24 cols)."""
    taxonomic_grid = (16, 24)
    offline_max_well_volume_ul = 90.0
    offline_first_well_mm = (12.13, 8.99, 0.0)
    offline_well_spacing_mm = (4.5, 4.5)
    offline_well_height_mm = 8.0
