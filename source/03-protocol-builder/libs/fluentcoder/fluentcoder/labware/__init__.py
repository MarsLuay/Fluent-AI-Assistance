"""Labware classes — first-class objects placeable on a Worktable.

v1.1 hierarchy (10 behavioral families):

- `Plate` (with `Plate96`/`Plate96Deep`/`Plate384`)
- `Trough` (with `Trough25mL`/`Trough100mL`/`Waste`)
- `TipBox` (with `MCA*Box`/`FCA*Box`)
- `MagnetRack`
- `TubeRack`
- `WashStation`, `WasteChute`, `Hotel`, `FixedDeck` — deck items
- `Adapter` (with `EvaAdapter`)
"""

from .base import (
    ExternalLabware, Labware, Layer, Well,
    CatalogIndexMissing,
)
from .plates import Plate, Plate96, Plate96Deep, Plate384
from .troughs import Trough, Trough25mL, Trough100mL, Waste
from .tipboxes import TipBox, TipLocation, MCA100Box, MCA200Box, MCA500Box, FCA50Box, FCA200Box, FCA1000Box
from .adapters import Adapter, EvaAdapter
from .magnet import MagnetRack
from .tuberack import TubeRack
from .deckitems import WashStation, WasteChute, Hotel, FixedDeck

__all__ = [
    "Labware", "ExternalLabware", "Layer", "Well", "CatalogIndexMissing",
    "Plate", "Plate96", "Plate96Deep", "Plate384",
    "Trough", "Trough25mL", "Trough100mL", "Waste",
    "TipBox", "TipLocation", "MCA100Box", "MCA200Box", "MCA500Box", "FCA50Box", "FCA200Box", "FCA1000Box",
    "Adapter", "EvaAdapter",
    "MagnetRack",
    "TubeRack",
    "WashStation", "WasteChute", "Hotel", "FixedDeck",
]


# Map category strings (from catalog inference) to their default Python class.
# Used by Worktable.from_workspace() for dispatching auto-placed labware.
CATEGORY_TO_CLASS: dict[str, type[Labware]] = {
    "plate": Plate,
    "trough": Trough,
    "tip_box": TipBox,
    "magnet_rack": MagnetRack,
    "tube_rack": TubeRack,
    "wash_station": WashStation,
    "waste_chute": WasteChute,
    "hotel": Hotel,
    "adapter": Adapter,
    "fixed_deck": FixedDeck,
}
