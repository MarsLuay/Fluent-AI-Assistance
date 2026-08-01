"""FCA (Fixed Channel Arm) authoring facade.

FCA pipetting uses the same LiHa IR step types and simulator path as
``wt.liha``. Liquid-class names must be supplied by the caller (recipe / ZEIA /
install catalog) — this facade does not invent Water Free Single / AcidExtract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence, Union

from ..expressions import Expression
from ..labware.base import Labware
from .liha import LiHa, LihaVolume

if TYPE_CHECKING:
    from ..worktable import Worktable


class FCAHead:
    """Eight-channel FCA pipetting head. Delegates to LiHa IR steps."""

    def __init__(self, worktable: "Worktable") -> None:
        self._wt = worktable

    @property
    def _liha(self) -> LiHa:
        return self._wt.liha

    def get_tips(
        self,
        labware: Optional[Union[Labware, str]] = None,
        *,
        tip_channels: Optional[Sequence[int]] = None,
        tip_index: Optional[int] = None,
    ) -> None:
        self._liha.get_tips(labware, tip_channels=tip_channels, tip_index=tip_index)

    def drop_tips(self, labware: Optional[Union[Labware, str]] = None) -> None:
        self._liha.drop_tips(labware)

    def aspirate(
        self,
        labware: Union[Labware, str],
        volume: Union[LihaVolume, Sequence[LihaVolume]],
        *,
        liquid_class: str,
        well_offset: Optional[Union[int, str, Expression]] = None,
    ) -> None:
        self._liha.aspirate(
            labware,
            volume,
            liquid_class=liquid_class,
            well_offset=well_offset,
        )

    def dispense(
        self,
        labware: Union[Labware, str],
        volume: Union[LihaVolume, Sequence[LihaVolume]],
        *,
        liquid_class: str,
        well_offset: Optional[Union[int, str, Expression]] = None,
    ) -> None:
        self._liha.dispense(
            labware,
            volume,
            liquid_class=liquid_class,
            well_offset=well_offset,
        )

    def mix(
        self,
        labware: Union[Labware, str],
        volume: Union[LihaVolume, Sequence[LihaVolume]],
        *,
        cycles: Union[int, str, Expression] = 10,
        liquid_class: str,
        well_offset: Optional[Union[int, str, Expression]] = None,
    ) -> None:
        self._liha.mix(
            labware,
            volume,
            cycles=cycles,
            liquid_class=liquid_class,
            well_offset=well_offset,
        )

    def empty_tips(
        self,
        labware: Union[Labware, str],
        volume: LihaVolume = 0,
        *,
        liquid_class: str,
    ) -> None:
        self._liha.empty_tips(
            labware,
            volume,
            liquid_class=liquid_class,
        )
