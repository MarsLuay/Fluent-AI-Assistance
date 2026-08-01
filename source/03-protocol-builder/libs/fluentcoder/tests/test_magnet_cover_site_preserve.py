"""Magnet cover normalization must keep ZEIA/IR magnet placements."""

from __future__ import annotations

from fluentcoder.compiler.renderer import Renderer
from fluentcoder.ir.schema import AddLabwareStep, Group, Protocol, RgaTransferLabwareStep


def _magnet_protocol(*, location: str, site: int) -> Protocol:
    return Protocol(
        name="Magnet Placement Preserve",
        worktable_guid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        worktable_name="Demo_Worktable_A",
        groups=[
            Group(
                name="Worktable Setup",
                steps=[
                    AddLabwareStep(
                        labware_type="LV_Alpaqua_A000350",
                        label="MagnetPlate",
                        location=location,
                        position=site,
                    ),
                    AddLabwareStep(
                        labware_type="96 Well Flat",
                        label="SamplePlate",
                        location="Demo_Nest_Pos",
                        position=1,
                    ),
                ],
            ),
            Group(
                name="Main",
                steps=[
                    RgaTransferLabwareStep(
                        labware_name="SamplePlate",
                        destination_location=location,
                        destination_site=site,
                    )
                ],
            ),
        ],
    )


def test_normalize_preserves_magnet_site_from_ir() -> None:
    """Do not invent Nest61mm_Pos site 3 — keep IR/ZEIA placement."""
    protocol = _magnet_protocol(location="Nest61mm_Pos", site=7)
    Renderer()._normalize_for_magnet_cover_site(protocol)

    magnet = protocol.groups[0].steps[0]
    transfer = protocol.groups[1].steps[0]
    assert magnet.location == "Nest61mm_Pos"
    assert Renderer._expression_python_value(magnet.position) == 7
    assert transfer.destination_location == "Nest61mm_Pos"
    assert Renderer._expression_python_value(transfer.destination_site) == 7


def test_normalize_preserves_non_nest61_magnet_location() -> None:
    protocol = _magnet_protocol(location="Demo_Magnet_Nest", site=2)
    Renderer()._normalize_for_magnet_cover_site(protocol)

    magnet = protocol.groups[0].steps[0]
    assert magnet.location == "Demo_Magnet_Nest"
    assert Renderer._expression_python_value(magnet.position) == 2
