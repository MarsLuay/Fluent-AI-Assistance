from fluentcoder.heads.mca_pickup_address import resolve_mca_pickup_address
from fluentcoder.ir.schema import PickUpTipsStep


def _step(**kwargs: object) -> PickUpTipsStep:
    return PickUpTipsStep(labware_name="synthetic tips", **kwargs)


def test_zero_orientation_keeps_authored_offsets_and_is_deterministic():
    step = _step(row=2, column=3, row_offset=1, column_offset=4, partial_rows=8, partial_columns=12)
    first = resolve_mca_pickup_address(step)
    second = resolve_mca_pickup_address(step)
    assert first["status"] == "resolved"
    assert first["base"] == {"row": 2, "column": 3}
    assert first["offsets"]["row"] == 1
    assert first["placement_orientation"] == {"phi": 0, "psi": 0, "theta": 0}
    assert first["fingerprint"] == second["fingerprint"]
    assert len(first["fingerprint"]) == 64


def test_known_zero_worktable_rotation_is_part_of_the_address_contract():
    resolved = resolve_mca_pickup_address(_step(row_offset=2), placement_orientation={"rotation": 0})
    assert resolved["status"] == "resolved"
    assert resolved["worktable_orientation"] == {"rotation": 0, "known": True}
    assert resolved["provenance"] == "mca_pickup_fields+worktable_placement"


def test_nonzero_worktable_rotation_cannot_determine_without_a_transform():
    rotated = resolve_mca_pickup_address(_step(), placement_orientation={"rotation": 180})
    assert rotated["status"] == "cannot_determine"
    assert "worktable placement rotation" in rotated["reason"]
    assert rotated["worktable_orientation"] == {"rotation": 180, "known": True}


def test_missing_worktable_rotation_cannot_determine_when_context_is_supplied():
    unknown = resolve_mca_pickup_address(_step(), placement_orientation={"rotation": None})
    assert unknown["status"] == "cannot_determine"
    assert "not available" in unknown["reason"]
    assert unknown["worktable_orientation"]["known"] is False


def test_nonzero_orientation_and_variables_cannot_determine():
    rotated = resolve_mca_pickup_address(_step(orientation_theta=180))
    assert rotated["status"] == "cannot_determine"
    assert "180" in rotated["reason"] or "nonzero" in rotated["reason"]
    assert rotated["placement_orientation"]["theta"] == 180

    variable = resolve_mca_pickup_address(_step(row="tip_row", orientation_phi="phi"))
    assert variable["status"] == "cannot_determine"
    assert variable["base"]["row"] == "tip_row"
    assert variable["placement_orientation"]["phi"] == "phi"
