"""MCA-384 pipetting head.

Authoring-side methods on this object emit one IR step per call. The
Simulator consumes the IR list and reconstructs head state (384-tip array)
from scratch — head methods themselves don't mutate twin state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

from ..expressions import Expression
from ..ir.schema import (
    AspirateStep,
    DispenseStep,
    Mca384DropTipsStep,
    Mca384EmptyTipsStep,
    Mca384GetTipsStep,
    Mca384MixStep,
    Mca384MoveArmStep,
)
from ..labware.base import Labware

if TYPE_CHECKING:
    from ..worktable import Worktable


class MCA384Head:
    """The MCA-384 pipetting head. Bound to a Worktable on construction."""

    def __init__(self, worktable: "Worktable") -> None:
        self._wt = worktable

    def _label(self, labware: Optional[Union[Labware, str]]) -> Optional[str]:
        if labware is None:
            return None
        return labware.label if isinstance(labware, Labware) else labware

    # ── Tips ────────────────────────────────────────────────────────

    def get_tips(self, tip_box: Optional[Union[Labware, str]] = None) -> None:
        self._wt._emit(Mca384GetTipsStep(labware_name=self._label(tip_box)))

    def drop_tips(self, tip_box: Optional[Union[Labware, str]] = None) -> None:
        self._wt._emit(Mca384DropTipsStep(labware_name=self._label(tip_box)))

    # ── Arm ─────────────────────────────────────────────────────────

    def move_arm(
        self,
        movement_type: str = "GlobalZTravel",
        labware: Optional[Union[Labware, str]] = None,
    ) -> None:
        self._wt._emit(Mca384MoveArmStep(
            movement_type=movement_type,
            labware_name=self._label(labware),
        ))

    # ── Pipetting ───────────────────────────────────────────────────

    def aspirate(
        self,
        target: Union[Labware, str],
        volume_ul: Union[float, int, str, Expression],
        *,
        liquid_class: str,
    ) -> None:
        """Aspirate from `target` (auto-parallel over the labware's wells).

        `liquid_class` is required and must be the exact FluentControl
        liquid-class name. No defaults are pulled from elsewhere.
        """
        self._wt._emit(AspirateStep(
            labware_name=self._label(target) or "",
            volume=volume_ul,
            liquid_class=liquid_class,
        ))

    def dispense(
        self,
        target: Union[Labware, str],
        volume_ul: Union[float, int, str, Expression],
        *,
        liquid_class: str,
    ) -> None:
        self._wt._emit(DispenseStep(
            labware_name=self._label(target) or "",
            volume=volume_ul,
            liquid_class=liquid_class,
        ))

    def mix(
        self,
        target: Union[Labware, str],
        volume_ul: Union[float, int, str, Expression],
        *,
        cycles: Union[int, str, Expression] = 10,
        liquid_class: str,
    ) -> None:
        self._wt._emit(Mca384MixStep(
            labware_name=self._label(target) or "",
            volume=volume_ul,
            cycles=cycles,
            liquid_class=liquid_class,
        ))

    def empty_tips(
        self,
        target: Union[Labware, str],
        volume_ul: Union[float, int, str, Expression],
        *,
        liquid_class: str | None = None,
    ) -> None:
        self._wt._emit(Mca384EmptyTipsStep(
            labware_name=self._label(target) or "",
            volume=volume_ul,
            liquid_class=liquid_class,
        ))
