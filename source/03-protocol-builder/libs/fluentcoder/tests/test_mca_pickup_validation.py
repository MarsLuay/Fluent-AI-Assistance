import pytest

from fluentcoder import MCA100Box, Worktable
from fluentcoder.heads.mca_pickup_address import validate_mca_pickup
from fluentcoder.ir.schema import PickUpTipsStep
from fluentcoder.simulator.invariants import MissingTipsError


def _step(**kwargs: object) -> PickUpTipsStep:
    return PickUpTipsStep(labware_name="Tips", partial_rows=8, partial_columns=12, **kwargs)


def test_resolved_zero_orientation_stays_logical_and_physical_unverified():
    decision = validate_mca_pickup(_step(row=1, column=2, row_offset=1))
    assert decision["logical_selection"] == "resolved"
    assert decision["precise_occupancy_allowed"] is True
    assert decision["findings"] == []
    assert decision["physical_readiness"]["status"] == "unverified"


def test_out_of_range_and_unprovable_offsets_are_review():
    wide = validate_mca_pickup(_step(row=7, row_offset=2))
    assert wide["findings"][0]["code"] == "out_of_bounds"
    assert wide["precise_occupancy_allowed"] is False

    missing_range = validate_mca_pickup(_step(row="tip_row"))
    assert {item["code"] for item in missing_range["findings"]} >= {"unresolved_mapping", "unprovable_range"}

    bounded = validate_mca_pickup(_step(row="tip_row"), loop_bounds={"tip_row": (0, 20)})
    assert any(item["code"] == "out_of_bounds" for item in bounded["findings"])


def test_worktable_rotation_is_reviewed_without_a_verified_transform():
    decision = validate_mca_pickup(_step(), placement_orientation={"rotation": 180})
    assert decision["address"]["status"] == "cannot_determine"
    assert decision["address"]["worktable_orientation"] == {"rotation": 180, "known": True}
    assert decision["findings"][0]["code"] == "orientation_unproven"
    assert decision["precise_occupancy_allowed"] is False


def test_orientation_drift_is_not_silently_zeroed():
    decision = validate_mca_pickup(_step(orientation_theta=180), target_orientation={"phi": 0, "psi": 0, "theta": 0})
    assert decision["address"]["status"] == "cannot_determine"
    assert any(item["code"] == "semantic_loss" for item in decision["findings"])


def test_simulator_refuses_rotated_placement_without_a_verified_transform():
    wt = Worktable(name="mca-placement-rotation")
    wt.group("Setup")
    tips = wt.place(
        MCA100Box("Tips", catalog="MCA96, 100ul, Box"),
        "Nest",
        1,
        rotation=180,
    )
    assert tips.placement_rotation == 180
    before = tips.available_tip_count
    wt.group("Pickup")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    with pytest.raises(MissingTipsError, match="unrotated"):
        wt.simulate()
    assert tips.available_tip_count == before


def test_simulator_tracks_set_location_rotation_before_pickup():
    wt = Worktable(name="mca-set-location-rotation")
    wt.group("Setup")
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 1)
    wt.set_location(tips, "Site", 2, rotation=180)
    assert tips.placement_rotation == 180
    before = tips.available_tip_count
    wt.group("Pickup")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    with pytest.raises(MissingTipsError, match="unrotated"):
        wt.simulate()
    assert tips.available_tip_count == before


def test_simulator_refuses_unrotated_occupancy_when_orientation_is_unknown():
    wt = Worktable(name="mca-address")
    wt.group("Setup")
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 1)
    before = tips.available_tip_count
    wt.group("Pickup")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips, orientation_theta=180)
    with pytest.raises(MissingTipsError, match="unrotated"):
        wt.simulate()
    assert tips.available_tip_count == before
