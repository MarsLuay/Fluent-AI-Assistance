"""Regression coverage for source-backed MCA pickup coordinates and offsets."""

from __future__ import annotations

from pathlib import Path

import pytest

from fluentcoder.compiler.renderer import Renderer
from fluentcoder.decompiler.codegen import emit_python
from fluentcoder.decompiler.xscr_parser import parse_xscr
from fluentcoder.ir.schema import AddLabwareStep, Group, PickUpTipsStep, Protocol


_WORKSPACE_GUID = "11111111-1234-aaaa-ffff-000000000222"
_WORKSPACE_NAME = "Synthetic Offline Workspace"
_DEVICE_ALIAS = "Instrument=1/Device=MCA384:1"
_AVAILABLE_ID = "USB:TECAN,FLUENT,2203009762/MCA384:1"


def _bound_protocol(step: PickUpTipsStep) -> Protocol:
    return Protocol(
        name="mca-pickup-offsets",
        groups=[Group(name="Steps", steps=[step])],
        worktable_guid=_WORKSPACE_GUID,
        worktable_name=_WORKSPACE_NAME,
    )


def _step(**kwargs: object) -> PickUpTipsStep:
    return PickUpTipsStep(
        labware_name="synthetic tips",
        device_alias=_DEVICE_ALIAS,
        available_id=_AVAILABLE_ID,
        **kwargs,
    )


@pytest.mark.usefixtures("synthetic_catalog")
def test_mca_pickup_offsets_round_trip_zero_nonzero_and_variable(tmp_path: Path) -> None:
    """Recognized MCA fields survive typed render and XSCR reparse."""
    original = _step(
        partial_column_offset=0,
        partial_rows_offset="partial_row_offset",
        well_offset=3,
        position_first_tip_x="first_tip_x",
        position_first_tip_y=1.25,
        first_tip_x_position=2,
        first_tip_y_position="first_tip_y_position",
        last_tip_x_position="last_tip_x_position",
        last_tip_y_position=8,
        column="tip_column",
        row=4,
        row_offset="tip_row_offset",
        column_offset=5,
        orientation_phi="phi",
        orientation_psi=0,
        orientation_theta=1,
        compartment=2,
        remove_rack=True,
        subsequent_pipetting_direction_is_row=True,
    )
    rendered = Renderer(deterministic=True).render(_bound_protocol(original))
    source = tmp_path / "pickup-offsets.xscr"
    source.write_text(rendered, encoding="utf-8")

    reparsed = parse_xscr(source).groups[0].steps[0]
    assert isinstance(reparsed, PickUpTipsStep)
    assert reparsed.partial_column_offset == 0
    assert reparsed.partial_rows_offset == "partial_row_offset"
    assert reparsed.well_offset == 3
    assert reparsed.position_first_tip_x == "first_tip_x"
    assert reparsed.position_first_tip_y == 1.25
    assert reparsed.first_tip_x_position == 2
    assert reparsed.first_tip_y_position == "first_tip_y_position"
    assert reparsed.last_tip_x_position == "last_tip_x_position"
    assert reparsed.last_tip_y_position == 8
    assert reparsed.column == "tip_column"
    assert reparsed.row == 4
    assert reparsed.row_offset == "tip_row_offset"
    assert reparsed.column_offset == 5
    assert reparsed.orientation_phi == "phi"
    assert reparsed.orientation_psi == 0
    assert reparsed.orientation_theta == 1
    assert reparsed.compartment == 2
    assert reparsed.remove_rack is True
    assert reparsed.subsequent_pipetting_direction_is_row is True

    rerendered = Renderer(deterministic=True).render(_bound_protocol(reparsed))
    for tag, value in {
        "PartialColumnOffset": "0",
        "PartialRowsOffset": "partial_row_offset",
        "WellOffset": "3",
        "RowOffset": "tip_row_offset",
        "ColumnOffset": "5",
        "OrientationPhi": "phi",
    }.items():
        assert f"<{tag}>{value}</{tag}>" in rerendered


@pytest.mark.usefixtures("synthetic_catalog")
def test_tip_box_placement_rotation_survives_xscr_round_trip(tmp_path: Path) -> None:
    protocol = Protocol(
        name="mca-placement-rotation",
        groups=[Group(
            name="Setup",
            steps=[AddLabwareStep(
                labware_type="MCA96, 100ul, Box",
                label="synthetic tips",
                location="Nest",
                position=1,
                rotation=180,
            )],
        )],
        worktable_guid=_WORKSPACE_GUID,
        worktable_name=_WORKSPACE_NAME,
    )
    rendered = Renderer(deterministic=True).render(protocol)
    source = tmp_path / "placement-rotation.xscr"
    source.write_text(rendered, encoding="utf-8")

    reparsed = parse_xscr(source).groups[0].steps[0]
    assert isinstance(reparsed, AddLabwareStep)
    assert reparsed.rotation == 180
    assert "<Rotation>180</Rotation>" in rendered


@pytest.mark.usefixtures("synthetic_catalog")
def test_mca_pickup_codegen_keeps_non_default_offsets() -> None:
    """Generated authoring code does not silently drop typed pickup offsets."""
    protocol = _bound_protocol(_step(row_offset="row_offset", column_offset=2))
    generated = emit_python(protocol)
    assert "row_offset='row_offset'" in generated
    assert "column_offset=2" in generated
