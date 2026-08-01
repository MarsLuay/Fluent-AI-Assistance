"""Tip-box labware. Tracks whether the box is full of fresh tips."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .base import Labware, well_grid_addresses


@dataclass
class TipLocation:
    """One physical tip position in a box."""

    address: str
    capacity_ul: float
    occupied: bool = True
    position_mm: Optional[tuple[float, float, float]] = None
    geometry: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.occupied

    def to_geometry(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "capacity_ul": self.capacity_ul,
            "occupied": self.occupied,
            "available": self.available,
            "used": not self.occupied,
        }
        if self.position_mm is not None:
            out["position_mm"] = {
                "x": self.position_mm[0],
                "y": self.position_mm[1],
                "z": self.position_mm[2],
            }
        out.update(self.geometry)
        return out


class TipBox(Labware):
    """Generic tip box. Subclasses fix the per-tip capacity."""

    category = "tip_box"
    taxonomic_grid = (0, 0)  # tip boxes are not iterated as wells in v1.1
    offline_max_well_volume_ul = 0.0
    offline_dim_mm = (127.76, 85.48, 55.0)
    offline_first_well_mm = (14.38, 11.24, 0.0)
    offline_well_spacing_mm = (9.0, 9.0)
    offline_well_shape = "Tip"
    offline_well_footprint = "round"
    offline_well_height_mm = 50.0
    capacity_ul: float = 0.0

    def _post_populate(self, *, catalog_entry, comp, max_well_volume_ul) -> None:
        source_wells = dict(self.wells)
        self.tip_locations: dict[str, TipLocation] = {}
        self._checked_out_tip_addresses: list[str] = []
        if source_wells:
            for address, well in source_wells.items():
                self.tip_locations[address] = TipLocation(
                    address=address,
                    capacity_ul=self.capacity_ul,
                    occupied=True,
                    position_mm=well.position_mm,
                    geometry=dict(well.geometry),
                )
        else:
            rows, cols = self._tip_grid()
            for address in well_grid_addresses(rows, cols):
                self.tip_locations[address] = TipLocation(
                    address=address,
                    capacity_ul=self.capacity_ul,
                    occupied=True,
                    position_mm=self._offline_well_position_mm(address),
                    geometry=self._offline_well_geometry(),
                )

        # Tip boxes don't have liquid wells; keep tips in tip_locations instead.
        self.wells = {}

    @property
    def is_full(self) -> bool:
        return bool(self.tip_locations) and all(tip.occupied for tip in self.tip_locations.values())

    @is_full.setter
    def is_full(self, value: bool) -> None:
        for tip in self.tip_locations.values():
            tip.occupied = bool(value)
        if value:
            self._checked_out_tip_addresses = []

    @property
    def tip_count(self) -> int:
        return len(self.tip_locations)

    @property
    def occupied_tip_count(self) -> int:
        return sum(1 for tip in self.tip_locations.values() if tip.occupied)

    @property
    def available_tip_count(self) -> int:
        return self.occupied_tip_count

    @property
    def used_tip_count(self) -> int:
        return self.tip_count - self.occupied_tip_count

    def consume_tips(self, count: int) -> list[str]:
        if count <= 0:
            return []
        available = self._available_addresses_column_major()
        if len(available) < count:
            raise ValueError(
                f"{self.label}: requested {count} tips but only {len(available)} are available"
            )
        return self.consume_tips_at_addresses(available[:count])

    def consume_tips_for_columns(self, columns: Iterable[int]) -> list[str]:
        return self.consume_tips_at_addresses(self._addresses_for_columns(columns))

    def consume_tips_at_addresses(self, addresses: Iterable[str]) -> list[str]:
        consumed: list[str] = []
        for address in addresses:
            tip = self.tip_locations.get(address)
            if tip is None:
                raise ValueError(f"{self.label}: unknown tip address {address!r}")
            if not tip.occupied:
                raise ValueError(f"{self.label}: tip {address!r} is not available")
            tip.occupied = False
            consumed.append(address)
        self._checked_out_tip_addresses.extend(consumed)
        return consumed

    def addresses_for_pickup(
        self,
        *,
        tip_columns: Optional[list[int]] = None,
        tip_count: Optional[int] = None,
        partial_columns: Optional[int] = None,
        partial_rows: Optional[int] = None,
    ) -> list[str]:
        """Resolve which tip positions a pickup step should mount."""
        rows, cols = self._tip_grid()
        if tip_columns is not None:
            return self._addresses_for_columns(tip_columns)
        if tip_count is not None:
            if tip_count <= 0:
                return []
            available = self._available_addresses_column_major()
            if len(available) < tip_count:
                raise ValueError(
                    f"{self.label}: requested {tip_count} tips but only {len(available)} are available"
                )
            return available[:tip_count]
        pc = partial_columns if partial_columns is not None else cols
        pr = partial_rows if partial_rows is not None else rows
        if pc >= cols and pr >= rows:
            return well_grid_addresses(rows, cols)
        addresses: list[str] = []
        for c in range(1, min(pc, cols) + 1):
            for r in range(min(pr, rows)):
                addresses.append(well_grid_addresses(rows, cols)[(c - 1) * rows + r])
        return addresses

    def _available_addresses_column_major(self) -> list[str]:
        rows, cols = self._tip_grid()
        ordered = well_grid_addresses(rows, cols)
        return [address for address in ordered if self.tip_locations[address].occupied]

    def _addresses_for_columns(self, columns: Iterable[int]) -> list[str]:
        rows, cols = self._tip_grid()
        ordered_cols = sorted(set(columns))
        addresses: list[str] = []
        for col in ordered_cols:
            if col < 1 or col > cols:
                raise ValueError(f"{self.label}: column {col} outside 1..{cols}")
            for r in range(rows):
                addresses.append(well_grid_addresses(rows, cols)[(col - 1) * rows + r])
        return addresses

    def return_tips(self, count: int) -> list[str]:
        if count <= 0:
            return []
        returned: list[str] = []
        while self._checked_out_tip_addresses and len(returned) < count:
            address = self._checked_out_tip_addresses.pop()
            if address in self.tip_locations and not self.tip_locations[address].occupied:
                self.tip_locations[address].occupied = True
                returned.append(address)
        if len(returned) < count:
            for address, tip in self.tip_locations.items():
                if len(returned) >= count:
                    break
                if not tip.occupied:
                    tip.occupied = True
                    returned.append(address)
        return returned

    def body_geometry(self) -> dict[str, Any]:
        body = super().body_geometry()
        rows, cols = self._tip_grid()
        source_grid = (self._pipettable_geometry or {}).get("grid") or {}
        body["grid"] = {
            "rows": rows,
            "columns": cols,
            "tip_count": self.tip_count,
            "x_spacing_mm": self._tip_spacing()[0],
            "y_spacing_mm": self._tip_spacing()[1],
            "first_tip_mm": source_grid.get("first_well_mm") or self.offline_first_well_mm,
        }
        body["tips"] = {
            address: tip.to_geometry()
            for address, tip in self.tip_locations.items()
        }
        body["tip_state"] = {
            "capacity_ul": self.capacity_ul,
            "occupied_count": self.occupied_tip_count,
            "available_count": self.available_tip_count,
            "used_count": self.used_tip_count,
            "is_full": self.is_full,
        }
        return body

    def _tip_grid(self) -> tuple[int, int]:
        if self._pipettable_geometry:
            grid = self._pipettable_geometry.get("grid") or {}
            rows = int(grid.get("rows") or 0)
            cols = int(grid.get("columns") or 0)
            if rows and cols:
                return rows, cols
        name = f"{self.catalog_name} {self.label}".lower()
        if "384" in name:
            return (16, 24)
        return (8, 12)

    def _tip_spacing(self) -> tuple[float, float]:
        if self._pipettable_geometry:
            grid = self._pipettable_geometry.get("grid") or {}
            x_spacing = grid.get("x_spacing_mm")
            y_spacing = grid.get("y_spacing_mm")
            if x_spacing is not None and y_spacing is not None:
                return float(x_spacing), float(y_spacing)
        rows, cols = self._tip_grid()
        if rows == 16 or cols == 24:
            return (4.5, 4.5)
        return self.offline_well_spacing_mm or (9.0, 9.0)


class MCA100Box(TipBox):
    capacity_ul = 100.0


class MCA200Box(TipBox):
    capacity_ul = 200.0


class MCA500Box(TipBox):
    capacity_ul = 500.0


class FCA50Box(TipBox):
    capacity_ul = 50.0


class FCA200Box(TipBox):
    capacity_ul = 200.0


class FCA1000Box(TipBox):
    capacity_ul = 1000.0
