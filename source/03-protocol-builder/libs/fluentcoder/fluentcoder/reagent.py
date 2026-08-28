"""Reagent identity model.

Reagents are first-class objects whose identity is the Python object reference.
The simulator tracks reagent identity through aspirate/dispense, layered well
contents, and tip carry-over.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, eq=False)
class Reagent:
    """A single reagent. Identity is `is`-based, not name-based.

    Two `Reagent("Ethanol 70%")` calls produce two distinct reagents — keep one
    canonical instance per reagent and reference it everywhere.
    """

    name: str
    pinned_when_magnetized: bool = False
    """When True, the simulator refuses bottom/explicit aspirate of this layer
    on a magnetized plate. Use for paramagnetic beads (AMPure, MyOne, etc.)."""

    metadata: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
    """Free-form metadata for downstream tooling. Not interpreted by the
    simulator; not rendered into IR."""

    def __repr__(self) -> str:
        flags = " pinned" if self.pinned_when_magnetized else ""
        return f"Reagent({self.name!r}{flags})"
