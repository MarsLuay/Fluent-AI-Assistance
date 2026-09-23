from __future__ import annotations

from fluent_pipeline.rga_routing import (
    DIRECT_SUPPORTED,
    MISSING_VECTOR_EVIDENCE,
    NO_COMMON_ROUTE,
    REGRIP_RESOLVED,
    TARGET_MISMATCH,
    analyze_rga_route,
)


def _site(guid: str, vectors: list[str], grips: list[str], *, evidence: str = "present") -> dict:
    return {
        "guid": guid,
        "allowed_vector_ids": vectors,
        "allowed_vector_evidence": evidence,
        "allowed_grip_modes": grips,
    }


def _catalog() -> dict:
    return {
        "fingerprint": "catalog-a",
        "vectors": [
            {"id": "v1", "end_position": {"x": 1, "y": 2, "z": 3}},
            {"id": "v2", "end_position": {"x": 4, "y": 5, "z": 6}},
        ],
    }


def test_shared_vector_and_grip_is_direct_and_ambiguous_candidates_are_retained():
    result = analyze_rga_route(
        _site("source", ["v1", "v2"], ["Narrow", "Wide"]),
        _site("destination", ["v1", "v2"], ["Narrow", "Wide"]),
        catalog=_catalog(),
    )

    assert result["status"] == DIRECT_SUPPORTED
    assert len(result["route_candidates"]) == 4
    assert result["selected_route"] is None
    assert "multiple_direct_routes_remain_ambiguous" in result["findings"]
    assert result["pathfinder"]["status"] == "not_evaluated"
    assert result["physical_verification"]["status"] == "required"


def test_regrip_is_source_backed_when_no_direct_route_exists():
    catalog = _catalog()
    catalog["regrip_routes"] = [
        {
            "source_site_guid": "source",
            "destination_site_guid": "destination",
            "station_id": "regrip-1",
            "vector_ids": ["v1"],
            "grip_modes": ["Wide"],
        }
    ]
    result = analyze_rga_route(
        _site("source", ["v1"], ["Narrow"]),
        _site("destination", ["v2"], ["Wide"]),
        catalog=catalog,
    )
    assert result["status"] == REGRIP_RESOLVED
    assert result["regrip_path"]["station_id"] == "regrip-1"


def test_missing_and_incompatible_routes_are_distinct():
    missing = analyze_rga_route(
        _site("source", [], [], evidence="unknown"),
        _site("destination", ["v1"], ["Narrow"]),
        catalog=_catalog(),
    )
    incompatible = analyze_rga_route(
        _site("source", ["v1"], ["Narrow"]),
        _site("destination", ["v2"], ["Wide"]),
        catalog=_catalog(),
    )
    assert missing["status"] == MISSING_VECTOR_EVIDENCE
    assert incompatible["status"] == NO_COMMON_ROUTE


def test_target_drift_and_endpoint_inconsistency_are_explicit():
    drift = analyze_rga_route(
        _site("source", ["v1"], ["Narrow"]),
        _site("destination", ["v1"], ["Narrow"]),
        catalog=_catalog(),
        target_catalog_fingerprint="catalog-b",
        expected_catalog_fingerprint="catalog-a",
    )
    inconsistent = analyze_rga_route(
        {**_site("source", ["v1"], ["Narrow"]), "placement_adjustment": {"x": 9, "y": 9, "z": 9}},
        _site("destination", ["v1"], ["Narrow"]),
        catalog=_catalog(),
    )
    assert drift["status"] == TARGET_MISMATCH
    assert "target_catalog_fingerprint_drift" in drift["unresolved"]
    assert "vector_endpoint_placement_inconsistent" in inconsistent["findings"]
