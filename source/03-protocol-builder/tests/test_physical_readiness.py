from __future__ import annotations

from copy import deepcopy

from fluent_pipeline.physical_readiness import (
    CHECK_REQUIRED,
    PHYSICAL_STATUS_UNKNOWN,
    build_hardware_profile,
    build_interaction_profiles,
    build_physical_readiness,
    build_physical_verification,
    physical_verification_is_stale,
)


def _ir(*, include_rga: bool = True, channels: list[int] | None = None) -> dict:
    steps = [
        {
            "id": "pick",
            "operation": "pick_up_tips",
            "target_labware": "Tips",
            "parameters": {"tip_channels": channels if channels is not None else list(range(8))},
        },
        {
            "id": "aspirate",
            "operation": "aspirate",
            "target_labware": "Plate",
            "parameters": {},
        },
    ]
    if include_rga:
        steps.append(
            {
                "id": "move",
                "operation": "move_plate",
                "target_labware": "Plate",
                "parameters": {"labware": "Plate", "destination_site": 2},
            }
        )
    return {
        "protocol": {"name": "Synthetic physical readiness"},
        "worktable": {"name": "SyntheticWorktable"},
        "dependencies": [{"kind": "device", "name": "MCA96"}],
        "labware": [
            {"label": "Tips", "catalog": "MCA96 Tips", "role": "tips", "location": "Carrier", "position": 1},
            {"label": "Plate", "catalog": "96 Well Flat", "role": "source", "location": "Carrier", "position": 2},
        ],
        "steps": steps,
        "source": {},
    }


def _manifest(*, tip_guid: str = "tip-guid", plate_diameter: float = 6.9) -> dict:
    return {
        "device_aliases": ["Instrument=1/Device=MCA96:1"],
        "available_ids": ["USB:TECAN,FLUENT,1/MCA96:1"],
        "worktable_geometry": {
            "components": [
                {
                    "guid": tip_guid,
                    "name": "MCA96 Tips",
                    "pipettable": {"rows": 8, "cols": 12, "pitch_x_mm": 9, "pitch_y_mm": 9},
                    "custom_attributes": {"PickupAlignment": "source-backed"},
                },
                {
                    "guid": "plate-guid",
                    "name": "96 Well Flat",
                    "dimension_mm": {"x": 127.8, "y": 85.5, "z": 14.4},
                    "pipettable": {
                        "rows": 8,
                        "cols": 12,
                        "pitch_x_mm": 9,
                        "pitch_y_mm": 9,
                        "well_diameter_mm": plate_diameter,
                        "well_depth_mm": 10.9,
                        "well_shape": "round",
                    },
                    "arrangements": [{"site_template_identifiers": {"0": "site-guid"}}],
                },
            ],
            "sites": [{"guid": "site-guid", "site_kind": "nest", "type_name": "Nest"}],
            "workspaces": [],
        },
    }


def _host(build: str = "399935", **extra: object) -> dict:
    return {
        "fingerprint": "volatile-host-fingerprint",
        "hostname": "not-relevant",
        "products": [{"family": "FluentControl", "version": "3.8 SP1", "build": build, "detected": True}],
        **extra,
    }


def test_unknown_identity_keeps_offline_pass_separate_from_hardware_run() -> None:
    report = build_physical_verification(_ir(include_rga=False), {}, offline_validation={"status": "passed"})

    assert report["offline_validation"]["status"] == "passed"
    assert report["hardware_run_ready"] is False
    assert report["hardware_profile"]["head"]["status"] == "source_backed"
    assert report["status"] == CHECK_REQUIRED
    assert all(item["status"] == CHECK_REQUIRED for item in report["checks"])


def test_empty_hardware_evidence_remains_unknown() -> None:
    profile = build_hardware_profile({"steps": []}, {})

    assert profile["status"] == "unknown"
    assert profile["head"]["status"] == "unknown"
    assert profile["tip"]["status"] == "unknown"
    assert profile["capabilities"]["status"] == "unknown"


def test_exact_source_profile_has_deterministic_interactions_and_check_ids() -> None:
    ir = _ir()
    manifest = _manifest()
    first = build_physical_verification(ir, manifest, host_environment=_host())
    second = build_physical_verification(ir, manifest, host_environment=_host())

    assert first["verification_fingerprint"] == second["verification_fingerprint"]
    assert [item["id"] for item in first["interactions"]] == [item["id"] for item in second["interactions"]]
    assert any(item["kind"] == "tip_pickup" for item in first["interactions"])
    assert any(item["effect"] == "nest_retention" for item in first["checks"])


def test_tip_guid_head_revision_and_relevant_geometry_change_fingerprints() -> None:
    base = build_hardware_profile(_ir(), _manifest(), host_environment=_host())
    tip_changed = build_hardware_profile(_ir(), _manifest(tip_guid="other-tip"), host_environment=_host())
    head_changed = build_hardware_profile(
        _ir(), {**_manifest(), "physical_hardware": {"head": {"family": "MCA96", "model": "MCA96-X", "revision": "B"}}}, host_environment=_host()
    )
    geometry_changed = build_hardware_profile(_ir(), _manifest(plate_diameter=3.0), host_environment=_host())

    assert base["fingerprint"] != tip_changed["fingerprint"]
    assert base["fingerprint"] != head_changed["fingerprint"]
    assert base["fingerprint"] != geometry_changed["fingerprint"]


def test_fluentcontrol_build_invalidates_but_unrelated_host_metadata_does_not() -> None:
    ir = _ir(include_rga=False)
    manifest = _manifest()
    current = build_physical_verification(ir, manifest, host_environment=_host())
    unrelated = build_physical_verification(ir, manifest, host_environment=_host(hostname="changed", fingerprint="changed"))
    changed = build_physical_verification(ir, manifest, host_environment=_host(build="different"))

    assert current["hardware_profile_fingerprint"] == unrelated["hardware_profile_fingerprint"]
    assert changed["hardware_profile_fingerprint"] != current["hardware_profile_fingerprint"]
    assert not physical_verification_is_stale(current, unrelated)
    assert physical_verification_is_stale(current, changed)


def test_rga_checks_are_relevant_only_to_rga_interactions() -> None:
    with_rga = build_physical_verification(_ir(include_rga=True), _manifest())
    without_rga = build_physical_verification(_ir(include_rga=False), _manifest())

    assert any(item["effect"] == "nest_retention" for item in with_rga["checks"])
    assert not any(item["effect"] == "nest_retention" for item in without_rga["checks"])
    assert with_rga["hardware_profile"]["capabilities"]["rga_required"] is True
    assert without_rga["hardware_profile"]["capabilities"]["rga_required"] is False


def test_partial_tip_pickup_is_a_distinct_interaction() -> None:
    full = build_interaction_profiles(_ir(channels=list(range(8))), build_hardware_profile(_ir(), _manifest()))
    partial = build_interaction_profiles(_ir(channels=[1, 2]), build_hardware_profile(_ir(), _manifest()))

    full_pickup = next(item for item in full if item["kind"] == "tip_pickup")
    partial_pickup = next(item for item in partial if item["kind"] == "tip_pickup")
    assert full_pickup["id"] != partial_pickup["id"]
    assert "partial_tip_pickup" in partial_pickup["unmodeled_effects"]


def test_operator_confirmed_evidence_is_retained() -> None:
    manifest = _manifest()
    manifest["physical_hardware"] = {
        "head": {"family": "MCA96", "model": "MCA96-X", "source": "operator-confirmed instrument plate"},
        "tip": {"guid": "operator-tip", "source": "operator-confirmed tip package"},
    }
    profile = build_hardware_profile(_ir(), manifest)

    evidence_sources = {item.get("source") for item in profile["evidence"]}
    assert "operator-confirmed instrument plate" in evidence_sources
    assert profile["head"]["model"] == "MCA96-X"


def test_source_provenance_is_retained_without_affecting_fingerprint() -> None:
    first_manifest = _manifest()
    first_manifest["physical_hardware"] = {
        "head": {
            "family": "MCA96",
            "provenance": {"source_archive": "source.zeia", "entry_path": "Objects/Head.xml"},
        }
    }
    second_manifest = deepcopy(first_manifest)
    second_manifest["physical_hardware"]["head"]["provenance"]["source_archive"] = "renamed.zeia"

    first = build_hardware_profile(_ir(), first_manifest)
    second = build_hardware_profile(_ir(), second_manifest)

    assert first["head"]["evidence"][0]["provenance"]["entry_path"] == "Objects/Head.xml"
    assert first["fingerprint"] == second["fingerprint"]


def test_forum_only_incompatibility_is_never_enforced() -> None:
    manifest = _manifest()
    manifest["physical_compatibility_rules"] = [
        {"id": "forum-claim", "status": "incompatible_by_verified_contract", "provenance": "forum report"}
    ]
    report = build_physical_readiness(_ir(), manifest)

    rule = report["compatibility_rules"][0]
    assert rule["status"] == PHYSICAL_STATUS_UNKNOWN
    assert rule["enforced"] is False


def test_verified_results_are_reused_only_on_same_fingerprint() -> None:
    initial = build_physical_verification(_ir(include_rga=False), _manifest())
    check_id = initial["checks"][0]["id"]
    verified = build_physical_verification(
        _ir(include_rga=False),
        _manifest(),
        verification_results={check_id: "verified"},
    )
    stale = build_physical_verification(
        _ir(include_rga=False),
        _manifest(tip_guid="new-tip"),
        previous_verification=verified,
    )

    assert next(item for item in verified["checks"] if item["id"] == check_id)["status"] == "verified"
    assert stale["status"] == CHECK_REQUIRED
    assert all(item["status"] != "verified" for item in stale["checks"])


def test_no_physical_interaction_is_not_applicable() -> None:
    ir = deepcopy(_ir(include_rga=False))
    ir["steps"] = [{"id": "comment", "operation": "comment", "parameters": {}}]
    report = build_physical_verification(ir, {})

    assert report["status"] == "not_applicable"
    assert report["checks"] == []
    assert report["hardware_run_ready"] is False
