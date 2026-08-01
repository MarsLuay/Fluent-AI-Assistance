"""Physical invariants — raised by the simulator on impossible operations.

These are *physics*, not domain rules. No keyword lists, no scenario logic.
"""


class SimulationError(Exception):
    """Base class for all simulator-raised invariant violations."""


class MissingTipsError(SimulationError):
    """Aspirate / dispense / mix attempted with no tips on the head."""


class MissingAdapterError(SimulationError):
    """MCA pipetting attempted with no adapter mounted."""


class InsufficientVolumeError(SimulationError):
    """A well does not have enough volume to satisfy an aspirate request."""


class OverdrawError(SimulationError):
    """A tip cannot dispense more than it currently holds."""


class OccupiedSlotError(SimulationError):
    """A slot is already occupied and the operation does not stack."""


class CannotAspirateError(SimulationError):
    """Aspirate target violates physical rules (e.g. pinned beads on magnet)."""


class MissingSimValueError(SimulationError):
    """A runtime variable's sim-time value was required but not provided."""


class InvalidSlotError(SimulationError):
    """`place()` targets a (location, position) not on the workspace."""
