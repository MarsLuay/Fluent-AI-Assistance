"""LiHa authoring helpers."""

from __future__ import annotations

from typing import Optional, Sequence, Union

from ..expressions import Expression
from ..ir.schema import (
    LihaAspirateStep,
    LihaDispenseStep,
    LihaDropTipsStep,
    LihaEmptyTipsStep,
    LihaGetTipsStep,
    LihaMixStep,
)
from ..labware.base import Labware

LihaVolume = Union[float, int, str, Expression]


class LiHa:
    """Liquid Handling Arm authoring facade."""

    def __init__(self, worktable) -> None:
        self.worktable = worktable

    @staticmethod
    def _label(labware: Optional[Union[Labware, str]]) -> Optional[str]:
        if labware is None:
            return None
        if isinstance(labware, str):
            return labware
        return labware.label

    def get_tips(
        self,
        labware: Optional[Union[Labware, str]] = None,
        *,
        tip_channels: Optional[Sequence[int]] = None,
        tip_index: Optional[int] = None,
    ) -> None:
        self.worktable._emit(
            LihaGetTipsStep(
                labware_name=self._label(labware),
                tip_channels=list(tip_channels) if tip_channels is not None else None,
                tip_index=tip_index if tip_channels is None else None,
            )
        )

    def drop_tips(self, labware: Optional[Union[Labware, str]] = None) -> None:
        self.worktable._emit(LihaDropTipsStep(labware_name=self._label(labware)))

    def aspirate(
        self,
        labware: Union[Labware, str],
        volume: Union[LihaVolume, Sequence[LihaVolume]],
        *,
        liquid_class: Optional[str] = None,
        well_offset: Optional[Union[int, str, Expression]] = None,
    ) -> None:
        volume_scalar, volumes = self._volume_args(volume)
        self.worktable._emit(
            LihaAspirateStep(
                labware_name=self._label(labware) or "",
                volume=volume_scalar,
                liquid_class=liquid_class,
                well_offset=well_offset,
                volumes=volumes,
            )
        )

    def dispense(
        self,
        labware: Union[Labware, str],
        volume: Union[LihaVolume, Sequence[LihaVolume]],
        *,
        liquid_class: Optional[str] = None,
        well_offset: Optional[Union[int, str, Expression]] = None,
    ) -> None:
        volume_scalar, volumes = self._volume_args(volume)
        self.worktable._emit(
            LihaDispenseStep(
                labware_name=self._label(labware) or "",
                volume=volume_scalar,
                liquid_class=liquid_class,
                well_offset=well_offset,
                volumes=volumes,
            )
        )

    def mix(
        self,
        labware: Union[Labware, str],
        volume: Union[LihaVolume, Sequence[LihaVolume]],
        *,
        cycles: Union[int, str, Expression] = 10,
        liquid_class: Optional[str] = None,
        well_offset: Optional[Union[int, str, Expression]] = None,
    ) -> None:
        volume_scalar, volumes = self._volume_args(volume)
        self.worktable._emit(
            LihaMixStep(
                labware_name=self._label(labware) or "",
                volume=volume_scalar,
                cycles=cycles,
                liquid_class=liquid_class,
                well_offset=well_offset,
                volumes=volumes,
            )
        )

    def empty_tips(
        self,
        labware: Union[Labware, str],
        volume: LihaVolume = 0,
        *,
        liquid_class: Optional[str] = None,
    ) -> None:
        self.worktable._emit(
            LihaEmptyTipsStep(
                labware_name=self._label(labware) or "",
                volume=volume,
                liquid_class=liquid_class,
            )
        )

    @staticmethod
    def _volume_args(volume: Union[LihaVolume, Sequence[LihaVolume]]) -> tuple[LihaVolume, list[LihaVolume] | None]:
        if isinstance(volume, (str, bytes)) or not isinstance(volume, Sequence):
            return volume, None
        volumes = list(volume)
        if not volumes:
            return 0, None
        return volumes[0], volumes
