"""MCA-96 pipetting head.

Authoring-side methods on this object emit one IR step per call. The
Simulator consumes the IR list and reconstructs head state (adapter, tip
contents) from scratch — head methods themselves don't mutate twin state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Union

from ..expressions import Expression
from ..ir.schema import (
    GetHeadAdapterStep, DropHeadAdapterStep,
    PickUpTipsStep, SetTipsBackStep,
    AspirateStep, DispenseStep, Mca384EmptyTipsStep, Mca384MixStep,
)
from ..labware.adapters import EvaAdapter
from ..labware.base import Labware

if TYPE_CHECKING:
    from ..reagent import Reagent
    from ..worktable import Worktable


@dataclass
class Tip:
    """One pipetting tip on the head. `layers` is FIFO when dispensing."""

    capacity_ul: float
    layers: list = field(default_factory=list)  # list[Layer]

    @property
    def volume_ul(self) -> float:
        return sum(layer.volume_ul for layer in self.layers)

    @property
    def is_empty(self) -> bool:
        return not self.layers


_DEFAULT_MCA_ADAPTER = "EVA[001]"


class MCA96Head:
    """The MCA-96 pipetting head. Bound to a Worktable on construction."""

    def __init__(self, worktable: "Worktable") -> None:
        self._wt = worktable

    # ── Adapter ─────────────────────────────────────────────────────

    def mount_adapter(self, adapter: Optional[EvaAdapter] = None) -> None:
        """Mount a head adapter.

        With no argument, references the default EVA catalog name directly —
        EVA does not need to be placed on the worktable; the FluentControl
        renderer treats it as a head accessory. Pass an explicit `EvaAdapter`
        instance to override the catalog name.
        """
        labware_name = adapter.catalog_name if adapter is not None else _DEFAULT_MCA_ADAPTER
        self._wt._emit(GetHeadAdapterStep(labware_name=labware_name))

    def drop_adapter(self, adapter: Optional[EvaAdapter] = None) -> None:
        labware_name = adapter.catalog_name if adapter is not None else _DEFAULT_MCA_ADAPTER
        self._wt._emit(DropHeadAdapterStep(labware_name=labware_name))

    # ── Tips ────────────────────────────────────────────────────────

    def _label(self, labware: Union[Labware, str]) -> str:
        return labware.label if isinstance(labware, Labware) else labware

    def pick_up(
        self,
        tip_box: Union[Labware, str],
        *,
        tip_columns: Optional[list[int]] = None,
        tip_count: Optional[int] = None,
        partial_columns: Optional[int] = None,
        partial_rows: Optional[int] = None,
    ) -> None:
        kwargs: dict = {"labware_name": self._label(tip_box)}
        if tip_columns is not None:
            kwargs["tip_columns"] = tip_columns
        if tip_count is not None:
            kwargs["tip_count"] = tip_count
        if partial_columns is not None:
            kwargs["partial_columns"] = partial_columns
        if partial_rows is not None:
            kwargs["partial_rows"] = partial_rows
        self._wt._emit(PickUpTipsStep(**kwargs))

    def return_tips(self, tip_box: Optional[Union[Labware, str]] = None) -> None:
        labware_name = self._label(tip_box) if tip_box is not None else None
        self._wt._emit(SetTipsBackStep(labware_name=labware_name))

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
            labware_name=self._label(target),
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
            labware_name=self._label(target),
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
            labware_name=self._label(target),
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
            labware_name=self._label(target),
            volume=volume_ul,
            liquid_class=liquid_class,
        ))
