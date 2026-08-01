"""MCA head adapters (e.g. EVA — Extended Volume Adapter).

Adapters live on the head, not on a worktable slot. They're modelled as
Labware so they can be referenced by catalog name; `place()`-ing one is
unusual (the renderer treats EVA as implicit).
"""

from __future__ import annotations

from typing import Any

from .base import Labware


class Adapter(Labware):
    """Generic head adapter."""
    category = "adapter"
    taxonomic_grid = (0, 0)
    offline_dim_mm = (108.0, 72.0, 18.0)

    def _body_shape(self) -> str:
        return "head_adapter"

    def body_geometry(self) -> dict[str, Any]:
        body = super().body_geometry()
        body["features"] = [
            {"role": "head_mount", "shape": "adapter_plate"},
            {"role": "tip_alignment", "shape": "pin_grid"},
        ]
        return body


class EvaAdapter(Adapter):
    """Extended-volume adapter for the MCA-96 head."""
    offline_dim_mm = (108.0, 72.0, 24.0)
