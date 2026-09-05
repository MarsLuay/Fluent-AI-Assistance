"""Labware base — Layer, Well, Labware.

`Labware` is the root for everything that occupies a worktable slot. Per-well
content is modelled as a list of `Layer` objects, bottom→top, so the
simulator can honestly track stratified contents.

In v1.1, per-catalog facts (well count, max volume, mm dimensions) are
loaded from the FluentControl install via the SQL catalog index. The class
itself only declares the *taxonomic* shape (`Plate96` → 8×12 wells); the
catalog tells you which catalog entry produces which facts.

When the catalog index is empty (CI / dev box without FC), classes
synthesize a generic default from their taxonomic shape so tests can still
run; mm geometry returns `None` and a one-shot warning is emitted.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Union

from ..fc_variables import FCVariableToken, encode_fc_variable

if TYPE_CHECKING:
    from ..reagent import Reagent


_ROW_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# ── Errors / warnings ──────────────────────────────────────────────


class CatalogIndexMissing(UserWarning):
    """Raised as a UserWarning when the catalog index is empty and a labware
    class falls back to synthesised defaults."""


_warned_offline_once = False


def _warn_offline_once() -> None:
    global _warned_offline_once
    if not _warned_offline_once:
        warnings.warn(
            "fluentcoder catalog index is empty; using synthesised offline defaults. "
            "Run `fluentcoder catalog refresh` for accurate per-catalog geometry.",
            CatalogIndexMissing,
            stacklevel=3,
        )
        _warned_offline_once = True


# ── Data types ─────────────────────────────────────────────────────


@dataclass
class Layer:
    reagent: "Reagent"
    volume_ul: float


@dataclass
class Well:
    address: str
    max_volume_ul: float
    layers: list[Layer] = field(default_factory=list)
    position_mm: Optional[tuple[float, float, float]] = None
    geometry: dict[str, Any] = field(default_factory=dict)
    cap_closed: bool = False

    @property
    def volume_ul(self) -> float:
        return sum(layer.volume_ul for layer in self.layers)

    @property
    def is_empty(self) -> bool:
        return not self.layers

    def add_layer(self, reagent: "Reagent", volume_ul: float) -> None:
        if volume_ul <= 0:
            return
        if self.layers and self.layers[-1].reagent is reagent:
            self.layers[-1].volume_ul += volume_ul
        else:
            self.layers.append(Layer(reagent=reagent, volume_ul=volume_ul))


def well_grid_addresses(rows: int, cols: int) -> list[str]:
    """Column-major: A1..H1, A2..H2, …"""
    out: list[str] = []
    for c in range(1, cols + 1):
        for r in range(rows):
            out.append(f"{_ROW_LETTERS[r]}{c}")
    return out


# ── Labware base ───────────────────────────────────────────────────


class Labware:
    """Base for any worktable-residing item.

    Subclasses declare the *taxonomic* shape via class attributes:

    - `category`: matches one of `fluentcoder.catalog.inference.CATEGORIES`.
    - `taxonomic_grid`: (rows, cols) for plate-shaped families. (0, 0)
      means "no fixed grid" (catalog determines).
    - `offline_max_well_volume_ul`: used only when the catalog index is
      empty.

    Per-catalog facts (well count if not fixed, max volume, mm dims, well
    positions) are loaded from the catalog row + .xcmp parse on construction.
    """

    category: str = "fixed_deck"
    taxonomic_grid: tuple[int, int] = (0, 0)
    offline_max_well_volume_ul: float = 0.0
    offline_dim_mm: Optional[tuple[float, float, float]] = None
    offline_first_well_mm: Optional[tuple[float, float, float]] = None
    offline_well_spacing_mm: Optional[tuple[float, float]] = None
    offline_well_shape: str = "Cylinder"
    offline_well_footprint: str = "round"
    offline_well_height_mm: Optional[float] = None

    def __init__(
        self,
        label: str,
        *,
        catalog: Optional[Union[str, FCVariableToken]] = None,
        max_well_volume_ul: Optional[float] = None,
    ) -> None:
        self.label: str = label
        self.slot: Optional[tuple[str, int]] = None
        self.stack_below: list[Labware] = []

        # Geometry / wells get filled in by one of two paths.
        self.catalog_name: str = ""
        self.dim_mm: Optional[tuple[float, float, float]] = None
        self.site_offsets_mm: tuple[tuple[float, float, float], ...] = ()
        self.wells: dict[str, Well] = {}
        self._pipettable_geometry: Optional[dict[str, Any]] = None
        self._arrangement_geometry: Optional[dict[str, Any]] = None
        self._component_geometry: dict[str, Any] = {}

        if isinstance(catalog, FCVariableToken):
            self.catalog_name = encode_fc_variable(catalog.name)
            self._populate_offline(max_well_volume_ul=max_well_volume_ul)
            return

        # Resolution rules:
        #   - index present + catalog given        → look up + parse xcmp.
        #   - index present + no catalog           → class default if registered,
        #     else raise (refuse to guess).
        #   - index missing + catalog given        → warn, offline synthesis.
        #   - index missing + no catalog           → offline synthesis silently
        #     (offline-fallback path; catalog name becomes `<offline:ClassName>`).
        from ..catalog.catalog import index_exists, resolve_by_name
        from ..defaults import get_catalog_default

        if index_exists():
            if not catalog:
                catalog = get_catalog_default(type(self))
            if not catalog:
                raise ValueError(
                    f"{type(self).__name__}({label!r}): the catalog index is built; "
                    f"you must pass `catalog=<exact FluentControl name>` or register "
                    f"a class default via `fluentcoder.defaults.set_catalog_default`. "
                    f"Run `fluentcoder catalog find <pattern>` to search."
                )
            entry = resolve_by_name(catalog)
            if entry is None:
                raise ValueError(
                    f"Catalog name {catalog!r} not found in fluentcoder catalog index. "
                    f"Run `fluentcoder catalog find {catalog!r}` to search."
                )
            self._populate_from_catalog(entry, max_well_volume_ul=max_well_volume_ul)
        else:
            if catalog:
                _warn_offline_once()
            self.catalog_name = catalog or self._offline_synthetic_catalog_name()
            self._populate_offline(max_well_volume_ul=max_well_volume_ul)

    # ── Catalog-driven population ─────────────────────────────────

    def _populate_from_catalog(self, entry, *, max_well_volume_ul: Optional[float]) -> None:
        from ..catalog.xcmp import load_xcmp

        self.catalog_name = entry.name
        if entry.dim_x_mm is not None:
            self.dim_mm = (entry.dim_x_mm, entry.dim_y_mm or 0.0, entry.dim_z_mm or 0.0)

        # Lazily parse the .xcmp for full geometry. Most callers won't need
        # site_offsets_mm or per-well positions, but they're cheap.
        try:
            comp = load_xcmp(entry.file_path)
        except Exception:
            comp = None
        if comp is not None:
            self._component_geometry = _strip_none_values({
                "guid": comp.guid,
                "name": comp.name,
                "functional_group": comp.functional_group,
                "footprint": comp.footprint,
                "renderer": comp.renderer,
                "is_lid": comp.is_lid,
                "mesh_object_names": list(comp.mesh_object_names),
                "sub_component_names": list(comp.sub_component_names),
                "site_guids": list(comp.site_guids),
                "custom_attrs": comp.custom_attrs,
            })

        # Pipettable wells override taxonomic_grid for any catalog that has them.
        if comp is not None and comp.pipettable is not None:
            pip = comp.pipettable
            self._pipettable_geometry = pip.well_geometry()
            cavity_vol = pip.cavity_volume_ul
            max_vol = (
                max_well_volume_ul
                if max_well_volume_ul is not None
                else (cavity_vol if cavity_vol is not None else self.offline_max_well_volume_ul)
            )
            well_geometry = _strip_none_values({
                "shape": self._pipettable_geometry.get("shape"),
                "footprint": self._pipettable_geometry.get("footprint"),
                "height_mm": self._pipettable_geometry.get("height_mm"),
                "volume_ul": self._pipettable_geometry.get("volume_ul"),
                "cavity_shapes": self._pipettable_geometry.get("cavity_shapes"),
            })
            for c in range(1, pip.x_wells + 1):
                for r in range(pip.y_wells):
                    addr = f"{_ROW_LETTERS[r]}{c}"
                    pos_mm = (
                        pip.first_well_mm[0] + (c - 1) * pip.x_spacing_mm,
                        pip.first_well_mm[1] + r * pip.y_spacing_mm,
                        pip.first_well_mm[2],
                    )
                    self.wells[addr] = Well(
                        address=addr,
                        max_volume_ul=max_vol,
                        position_mm=pos_mm,
                        geometry=dict(well_geometry),
                    )

        if comp is not None and comp.arrangement is not None:
            arr = comp.arrangement
            self._arrangement_geometry = {
                "sites_in_x": arr.sites_in_x,
                "sites_in_y": arr.sites_in_y,
                "sites_in_z": arr.sites_in_z,
                "site_spacing_mm": arr.site_spacing_mm,
                "position_in_parent_mm": arr.position_in_parent_mm,
                "site_count": arr.site_count,
                "site_offsets_mm": [
                    _vec3_to_dict(arr.site_offsets_mm[index])
                    for index in sorted(arr.site_offsets_mm)
                ],
                "allowed_grip_modes": {
                    str(index): list(modes)
                    for index, modes in sorted(arr.allowed_grip_modes.items())
                },
            }
            self.site_offsets_mm = tuple(
                arr.site_offsets_mm.get(i, (0.0, 0.0, 0.0))
                for i in range(arr.site_count)
            )

        if self.dim_mm is None and comp is not None:
            self.dim_mm = comp.dim_mm

        if not self.wells and self.category != "tip_box":
            self._populate_from_catalog_grid_fallback(
                entry,
                max_well_volume_ul=max_well_volume_ul,
            )

        # Subclass hook for non-plate populations (Trough single-pool, TipBox
        # is_full, etc.) — invoked AFTER pipettable wells are populated so the
        # subclass can override or supplement.
        self._post_populate(catalog_entry=entry, comp=comp,
                            max_well_volume_ul=max_well_volume_ul)

    def _populate_from_catalog_grid_fallback(self, entry, *, max_well_volume_ul: Optional[float]) -> None:
        cols = int(getattr(entry, "grid_x", 0) or 0)
        rows = int(getattr(entry, "grid_y", 0) or 0)
        if not rows or not cols:
            rows, cols = self.taxonomic_grid
        if not rows or not cols:
            return

        first_well = self.offline_first_well_mm or (0.0, 0.0, 0.0)
        spacing = self.offline_well_spacing_mm or _infer_grid_spacing(rows, cols, self.dim_mm)
        self._pipettable_geometry = {
            "grid": {
                "rows": rows,
                "columns": cols,
                "x_spacing_mm": spacing[0],
                "y_spacing_mm": spacing[1],
                "well_count": rows * cols,
                "first_well_mm": first_well,
            },
            "shape": self.offline_well_shape,
            "footprint": self.offline_well_footprint,
            "height_mm": self.offline_well_height_mm,
            "volume_ul": max_well_volume_ul if max_well_volume_ul is not None else self.offline_max_well_volume_ul,
            "cavity_shapes": [],
        }
        max_vol = (
            max_well_volume_ul
            if max_well_volume_ul is not None
            else self.offline_max_well_volume_ul
        )
        well_geometry = self._offline_well_geometry()
        for c in range(1, cols + 1):
            for r in range(rows):
                addr = f"{_ROW_LETTERS[r]}{c}"
                pos_mm = (
                    first_well[0] + (c - 1) * spacing[0],
                    first_well[1] + r * spacing[1],
                    first_well[2],
                )
                self.wells[addr] = Well(
                    address=addr,
                    max_volume_ul=max_vol,
                    position_mm=pos_mm,
                    geometry=dict(well_geometry),
                )

    # ── Offline synthesis ─────────────────────────────────────────

    def _populate_offline(self, *, max_well_volume_ul: Optional[float]) -> None:
        rows, cols = self.taxonomic_grid
        max_vol = (
            max_well_volume_ul
            if max_well_volume_ul is not None
            else self.offline_max_well_volume_ul
        )
        self.dim_mm = self.offline_dim_mm
        if rows and cols:
            for addr in well_grid_addresses(rows, cols):
                position_mm = self._offline_well_position_mm(addr)
                self.wells[addr] = Well(
                    address=addr,
                    max_volume_ul=max_vol,
                    position_mm=position_mm,
                    geometry=self._offline_well_geometry(),
                )
        # Subclasses can override / supplement.
        self._post_populate(catalog_entry=None, comp=None,
                            max_well_volume_ul=max_well_volume_ul)

    def _offline_synthetic_catalog_name(self) -> str:
        """Catalog name used in IR steps when no real catalog row was resolved."""
        return f"<offline:{type(self).__name__}>"

    # ── Subclass hook ─────────────────────────────────────────────

    def _post_populate(self, *, catalog_entry, comp, max_well_volume_ul) -> None:
        """Subclasses extend this to set family-specific state.

        Default: no-op. `Trough` builds a single-pool well; `TipBox` sets
        `is_full = True` and `capacity_ul`; etc.
        """
        return

    # ── Render/body geometry ──────────────────────────────────────

    def body_geometry(self) -> dict[str, Any]:
        """Serializable body facts for deck renderers and reports.

        The simulator does not render 3D itself, but this gives callers enough
        model-specific geometry to draw real wells, tip locations, carriers,
        hotels, and deck accessories instead of anonymous boxes.
        """
        return _strip_none_values({
            "kind": self.category,
            "body_shape": self._body_shape(),
            "catalog_name": self.catalog_name,
            "component": self._component_geometry or None,
            "dimensions_mm": _vec3_to_dict(self.dim_mm),
            "slot": list(self.slot) if self.slot else None,
            "grid": self._grid_geometry(),
            "wells": {
                address: self._well_body_geometry(well)
                for address, well in self.wells.items()
            },
            "arrangement": self._arrangement_geometry,
            "sites": self._site_geometry(),
        })

    def _body_shape(self) -> str:
        name = f"{self.catalog_name} {self.label}".lower()
        if "cap holder" in name or "capholder" in name:
            return "cap_holder"
        if self.category == "fixed_deck" and self.site_offsets_mm:
            return "carrier"
        return self.category

    def _grid_geometry(self) -> Optional[dict[str, Any]]:
        if self._pipettable_geometry:
            return self._pipettable_geometry.get("grid")
        rows, cols = self._effective_grid()
        if not rows or not cols:
            return None
        grid: dict[str, Any] = {
            "rows": rows,
            "columns": cols,
            "well_count": rows * cols,
        }
        if self.offline_well_spacing_mm is not None:
            grid["x_spacing_mm"] = self.offline_well_spacing_mm[0]
            grid["y_spacing_mm"] = self.offline_well_spacing_mm[1]
        if self.offline_first_well_mm is not None:
            grid["first_well_mm"] = self.offline_first_well_mm
        return grid

    def _well_body_geometry(self, well: Well) -> dict[str, Any]:
        return _strip_none_values({
            "position_mm": _vec3_to_dict(well.position_mm),
            "max_volume_ul": well.max_volume_ul,
            **well.geometry,
        })

    def _site_geometry(self) -> list[dict[str, Any]]:
        return [
            {
                "index": i + 1,
                "position_mm": _vec3_to_dict(offset),
            }
            for i, offset in enumerate(self.site_offsets_mm)
        ]

    def _offline_well_geometry(self) -> dict[str, Any]:
        return _strip_none_values({
            "shape": self.offline_well_shape,
            "footprint": self.offline_well_footprint,
            "height_mm": self.offline_well_height_mm,
        })

    def _offline_well_position_mm(self, address: str) -> Optional[tuple[float, float, float]]:
        if self.offline_first_well_mm is None or self.offline_well_spacing_mm is None:
            return None
        row_idx, col_idx = _well_address_indexes(address)
        if row_idx is None or col_idx is None:
            return None
        return (
            self.offline_first_well_mm[0] + col_idx * self.offline_well_spacing_mm[0],
            self.offline_first_well_mm[1] + row_idx * self.offline_well_spacing_mm[1],
            self.offline_first_well_mm[2],
        )

    # ── Well selection ────────────────────────────────────────────

    def well(self, address: str) -> Well:
        if address not in self.wells:
            raise KeyError(f"{self.label}: no well at address {address!r}")
        return self.wells[address]

    def all_wells(self) -> list[Well]:
        return list(self.wells.values())

    def column(self, idx: int) -> list[Well]:
        rows, cols = self._effective_grid()
        if not rows or idx < 1 or idx > cols:
            raise ValueError(f"{self.label}: column {idx} out of range (1..{cols})")
        return [self.wells[f"{_ROW_LETTERS[r]}{idx}"] for r in range(rows)]

    def row(self, letter: str) -> list[Well]:
        rows, cols = self._effective_grid()
        letter = letter.upper()
        if letter not in _ROW_LETTERS[:rows]:
            raise ValueError(f"{self.label}: row {letter!r} out of range")
        return [self.wells[f"{letter}{c}"] for c in range(1, cols + 1)]

    def _effective_grid(self) -> tuple[int, int]:
        if self.taxonomic_grid != (0, 0):
            return self.taxonomic_grid
        # Derive from wells if no taxonomic grid set.
        if not self.wells:
            return (0, 0)
        # Try to back-derive (rows, cols) from addresses like 'A1', 'H12'.
        rows = max(_ROW_LETTERS.index(a[0]) for a in self.wells if a[0] in _ROW_LETTERS) + 1
        cols = max(int(a[1:]) for a in self.wells if a[1:].isdigit())
        return (rows, cols)

    def fill_all(self, reagent: "Reagent", volume_ul: float) -> None:
        for w in self.wells.values():
            w.layers = [Layer(reagent=reagent, volume_ul=volume_ul)]

    # ── Stacking / state ──────────────────────────────────────────

    @property
    def is_magnetized(self) -> bool:
        from .magnet import MagnetRack
        return any(isinstance(x, MagnetRack) for x in self.stack_below)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.label!r}, catalog={self.catalog_name!r})"


class ExternalLabware(Labware):
    """Placeholder for exact FluentControl catalog names outside the local index."""

    def __init__(self, label: str, *, catalog: str, max_well_volume_ul: Optional[float] = None) -> None:
        self.label: str = label
        self.slot: Optional[tuple[str, int]] = None
        self.stack_below: list[Labware] = []
        self.catalog_name: str = catalog
        self.dim_mm: Optional[tuple[float, float, float]] = None
        self.site_offsets_mm: tuple[tuple[float, float, float], ...] = ()
        self.wells: dict[str, Well] = {}
        self._pipettable_geometry: Optional[dict[str, Any]] = None
        self._arrangement_geometry: Optional[dict[str, Any]] = None
        self._component_geometry: dict[str, Any] = {}


def _well_address_indexes(address: str) -> tuple[Optional[int], Optional[int]]:
    if not address:
        return None, None
    row = address[0].upper()
    col = address[1:]
    if row not in _ROW_LETTERS or not col.isdigit():
        return None, None
    return _ROW_LETTERS.index(row), int(col) - 1


def _vec3_to_dict(value: Optional[tuple[float, float, float]]) -> Optional[dict[str, float]]:
    if value is None:
        return None
    return {"x": value[0], "y": value[1], "z": value[2]}


def _infer_grid_spacing(
    rows: int,
    cols: int,
    dim_mm: Optional[tuple[float, float, float]],
) -> tuple[float, float]:
    if rows == 16 or cols == 24:
        return (4.5, 4.5)
    if rows == 8 or cols == 12:
        return (9.0, 9.0)
    if dim_mm is not None and rows > 1 and cols > 1:
        return (dim_mm[0] / max(cols, 1), dim_mm[1] / max(rows, 1))
    return (0.0, 0.0)


def _strip_none_values(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}
