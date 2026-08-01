from fluent_pipeline.tube_fixing_harness import TUBE_FIXING_CASES, tube_fixing_case_matrix


def test_tube_fixing_matrix_is_the_bounded_two_by_two_grid() -> None:
    assert [(case.grip_cap_close, case.cap_seat_z_offset) for case in TUBE_FIXING_CASES] == [
        (25, 8.5),
        (25, 7.5),
        (33, 8.5),
        (33, 7.5),
    ]
    assert [case["case_id"] for case in tube_fixing_case_matrix()] == [1, 2, 3, 4]
    assert TUBE_FIXING_CASES[-1].source_status == "experimental-cross-combination"
