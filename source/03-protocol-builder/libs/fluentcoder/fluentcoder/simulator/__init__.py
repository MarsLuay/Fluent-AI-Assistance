"""Simulator — walks the protocol IR and reconstructs a snapshotted twin."""

from .invariants import (
    SimulationError,
    MissingTipsError, InsufficientVolumeError, OverdrawError,
    OccupiedSlotError, CannotAspirateError, MissingAdapterError,
    MissingSimValueError, InvalidSlotError,
)
from .snapshots import Snapshot
from .report import EffectKind, SimulationFailure, SimulationReport, StepCoverage
from .walk import Simulator

__all__ = [
    "Simulator", "Snapshot", "SimulationReport", "SimulationFailure", "StepCoverage", "EffectKind",
    "SimulationError",
    "MissingTipsError", "InsufficientVolumeError", "OverdrawError",
    "OccupiedSlotError", "CannotAspirateError", "MissingAdapterError",
    "MissingSimValueError", "InvalidSlotError",
]
