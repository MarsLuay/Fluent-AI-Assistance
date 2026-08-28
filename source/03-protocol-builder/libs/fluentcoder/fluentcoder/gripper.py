"""Gripper — moves labware between worktable slots, supports stacking."""

from typing import TYPE_CHECKING, Optional, Union

from .expressions import Expression
from .ir.schema import (
    CgaGetFingersStep, CgaDropFingersStep, RgaTransferLabwareStep,
)
from .labware.base import Labware

if TYPE_CHECKING:
    from .worktable import Worktable


class Gripper:
    """Robotic gripper attached to a Worktable.

    `move(plate, to=(loc, pos))` performs a plain slot move.
    `move(plate, onto=other_labware)` stacks the plate on top of another
    labware that is already on the worktable (e.g. a magnet rack).
    """

    def __init__(self, worktable: "Worktable") -> None:
        self._wt = worktable

    def move(
        self,
        labware: Union[Labware, str],
        *,
        to: Optional[tuple[str, Union[int, str, Expression]]] = None,
        onto: Optional[Labware] = None,
        module_name: Optional[str] = None,
        available_id: Optional[str] = None,
    ) -> None:
        if (to is None) == (onto is None):
            raise TypeError("Gripper.move requires exactly one of `to=` or `onto=`")

        if onto is not None:
            if onto.slot is None:
                raise ValueError(
                    f"Cannot stack onto {onto.label!r}: it is not placed on the worktable"
                )
            dest_loc, dest_pos = onto.slot
        else:
            dest_loc, dest_pos = to  # type: ignore[misc]

        labware_name = labware.label if isinstance(labware, Labware) else labware
        rga_module = (
            (module_name if module_name is not None else None)
            or getattr(self._wt, "rga_module_name", None)
            or ""
        )
        rga_available = (
            available_id
            if available_id is not None
            else getattr(self._wt, "available_id", None)
        )
        self._wt._emit(CgaGetFingersStep(labware_name=labware_name))
        self._wt._emit(RgaTransferLabwareStep(
            labware_name=labware_name,
            destination_location=dest_loc,
            destination_site=dest_pos,
            module_name=str(rga_module or ""),
            available_id=rga_available,
        ))
        self._wt._emit(CgaDropFingersStep(labware_name=labware_name))

    def get_fingers(
        self,
        *,
        labware_name: Optional[str] = None,
        device_alias: Optional[str] = None,
        available_id: Optional[str] = None,
    ) -> None:
        """Emit a standalone CGA get-fingers command without an RGA move."""
        self._wt._emit(CgaGetFingersStep(
            labware_name=labware_name,
            device_alias=device_alias,
            available_id=available_id,
        ))

    def drop_fingers(
        self,
        *,
        labware_name: Optional[str] = None,
        device_alias: Optional[str] = None,
        available_id: Optional[str] = None,
        use_source_as_back_position: str = "BackToPosition",
    ) -> None:
        """Emit a standalone CGA drop-fingers command without an RGA move."""
        self._wt._emit(CgaDropFingersStep(
            labware_name=labware_name,
            device_alias=device_alias,
            available_id=available_id,
            use_source_as_back_position=use_source_as_back_position,
        ))
