"""fluentcoder — stateful object-model framework for FluentControl protocols."""

# Build the catalog index on first import (one-time slow startup, ~5–15 s
# on a 629-component install). Quiet no-op if the index is already there.
from .catalog import ensure_index as _ensure_index
_ensure_index()

from .fc_variables import FCVariableToken
from .expressions import (
    ReviewedRawExpression,
    SourcePreservedExpression,
    expression_python_value,
    parse_expression,
    render_expression,
)
from .reagent import Reagent
from .ir.schema import VariableMapping
from .worktable import Worktable, VariableExportOptions, VariableImportOptions
from .gripper import Gripper
from .heads import FCAHead, LiHa, MCA96Head, MCA384Head, Tip
from .labware import (
    Labware, ExternalLabware, Layer, Well,
    Plate, Plate96, Plate96Deep, Plate384,
    Trough, Trough25mL, Trough100mL, Waste,
    TipBox, MCA100Box, MCA200Box, MCA500Box,
    FCA50Box, FCA200Box, FCA1000Box,
    EvaAdapter, MagnetRack,
    TubeRack, WashStation, WasteChute, Hotel, Adapter, FixedDeck,
)
from .simulator import (
    Simulator, Snapshot, SimulationReport, StepCoverage, EffectKind,
    SimulationError,
    MissingTipsError, InsufficientVolumeError, OverdrawError,
    OccupiedSlotError, CannotAspirateError, MissingAdapterError,
    MissingSimValueError, InvalidSlotError,
)
from .labware import CatalogIndexMissing
from . import defaults

__all__ = [
    "FCVariableToken",
    "ReviewedRawExpression",
    "SourcePreservedExpression",
    "expression_python_value",
    "parse_expression",
    "render_expression",
    "Reagent",
    "VariableMapping",
    "Worktable", "VariableExportOptions", "VariableImportOptions", "Gripper",
    "MCA96Head", "MCA384Head", "LiHa", "FCAHead", "Tip",
    "Labware", "ExternalLabware", "Layer", "Well",
    "Plate", "Plate96", "Plate96Deep", "Plate384",
    "Trough", "Trough25mL", "Trough100mL", "Waste",
    "TipBox", "MCA100Box", "MCA200Box", "MCA500Box",
    "FCA50Box", "FCA200Box", "FCA1000Box",
    "EvaAdapter", "MagnetRack",
    "TubeRack", "WashStation", "WasteChute", "Hotel", "Adapter", "FixedDeck",
    "Simulator", "Snapshot", "SimulationReport", "StepCoverage", "EffectKind",
    "SimulationError",
    "MissingTipsError", "InsufficientVolumeError", "OverdrawError",
    "OccupiedSlotError", "CannotAspirateError", "MissingAdapterError",
    "MissingSimValueError", "InvalidSlotError",
    "CatalogIndexMissing",
    "defaults",
]
