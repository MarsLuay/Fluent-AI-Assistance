"""Deck items — wash stations, waste chutes, hotels, fixed-deck infrastructure.

These don't carry pipetted reagents in v1.1 (or, in the case of WashStation,
liquid is flushed away rather than tracked). They populate a worktable slot,
satisfy gripper-move destinations, and surface mm geometry — but no per-well
state tracking.
"""

from __future__ import annotations

from typing import Any

from .base import Labware


class WashStation(Labware):
    """Tip-wash station. Aspirate/dispense here is a flush, not a transfer."""
    category = "wash_station"
    taxonomic_grid = (0, 0)
    offline_dim_mm = (156.0, 40.0, 80.0)

    def body_geometry(self) -> dict[str, Any]:
        body = super().body_geometry()
        body["features"] = [
            {"role": "wash_channel", "shape": "rectangular_trough"},
            {"role": "rinse_posts", "shape": "pin_array"},
        ]
        return body


class WasteChute(Labware):
    """Sink for empty-tips / waste-dispense."""
    category = "waste_chute"
    taxonomic_grid = (0, 0)
    offline_dim_mm = (75.0, 30.0, 155.0)

    def body_geometry(self) -> dict[str, Any]:
        body = super().body_geometry()
        body["features"] = [
            {"role": "waste_opening", "shape": "vertical_slot"},
        ]
        return body


class Hotel(Labware):
    """Multi-z plate storage. Gripper destination."""
    category = "hotel"
    taxonomic_grid = (0, 0)
    offline_dim_mm = (140.0, 95.0, 180.0)

    def body_geometry(self) -> dict[str, Any]:
        body = super().body_geometry()
        shelf_count = max(len(self.site_offsets_mm), 1)
        body["features"] = [
            {
                "role": "storage_shelves",
                "shape": "vertical_shelf_stack",
                "count": shelf_count,
            }
        ]
        return body


class FixedDeck(Labware):
    """Catch-all for unknown / fixed deck items.

    Loads geometry but no behavior — used by `Worktable.from_workspace` for
    components whose category is `fixed_deck`.
    """
    category = "fixed_deck"
    taxonomic_grid = (0, 0)
    offline_dim_mm = (127.76, 85.48, 15.0)

    def body_geometry(self) -> dict[str, Any]:
        body = super().body_geometry()
        shape = body.get("body_shape")
        if shape == "carrier":
            body["features"] = [
                {
                    "role": "carrier_sites",
                    "shape": "site_rail",
                    "count": len(self.site_offsets_mm),
                }
            ]
        elif shape == "cap_holder":
            body["features"] = [
                {
                    "role": "cap_positions",
                    "shape": "cap_socket_grid",
                }
            ]
        return body
