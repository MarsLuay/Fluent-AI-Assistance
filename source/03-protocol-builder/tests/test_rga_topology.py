from __future__ import annotations

from fluent_pipeline.rga_topology import (
    AMBIGUOUS,
    INVALID,
    VERIFIED,
    UNKNOWN,
    analyze_storage_transition,
    normalize_rga_topology,
)


def test_preserves_stack_lid_relation_orientation_and_occupancy():
    topology = normalize_rga_topology(
        {
            "guid": "carrier-1",
            "carrier_type": "source-backed-stacker",
            "stack_relationships": [
                {
                    "parent_id": "lid-1",
                    "child_id": "plate-1",
                    "relationship": "lid_as_labware",
                    "site_id": "site-1",
                    "orientation": 180,
                    "level": 1,
                    "source_provenance": {"member": "Stack"},
                }
            ],
            "storage_sites": [{"guid": "site-1", "site_index": 0, "order": 0, "transfer_site_id": "transfer-1"}],
            "occupancy": [{"site_id": "site-1", "labware_id": "plate-1", "stack_level": 1, "orientation": 180}],
        }
    )
    assert topology["status"] == VERIFIED
    assert topology["stack_relationships"][0]["relationship"] == "lid_as_labware"
    assert topology["occupancy"][0]["orientation"] == 180
    assert topology["storage_sites"][0]["transfer_site_id"] == "transfer-1"


def test_explicit_storage_order_is_deterministic_and_transition_keeps_route_boundary():
    topology = normalize_rga_topology(
        {
            "storage_sites": [
                {"guid": "site-2", "site_index": 2, "order": 1},
                {"guid": "site-1", "site_index": 1, "order": 0},
            ]
        }
    )
    transition = analyze_storage_transition(
        topology,
        source_site_id="site-1",
        destination_site_id="site-2",
        labware_id="plate-1",
    )
    assert topology["storage_order_status"] == VERIFIED
    assert transition["status"] == "logical_transition_supported_by_source"
    assert transition["rga_route_status"] == "not_evaluated"
    assert transition["physical_verification"] == "required"


def test_missing_or_name_only_storage_is_unknown_not_inferred():
    assert normalize_rga_topology({"carrier_name": "Stacker by name only"})["status"] == UNKNOWN
    topology = normalize_rga_topology({"storage_sites": [{"name": "stack 1", "order": 0}]})
    assert topology["status"] == AMBIGUOUS
    assert "storage_site_unresolved" in topology["diagnostics"]


def test_duplicate_order_and_capacity_overflow_are_reviewable():
    topology = normalize_rga_topology(
        {
            "capacity": 1,
            "storage_sites": [
                {"guid": "site-1", "order": 0},
                {"guid": "site-2", "order": 0},
            ],
            "occupancy": [{"site_id": "site-1", "labware_id": "a"}, {"site_id": "site-2", "labware_id": "b"}],
        }
    )
    assert topology["status"] == INVALID
    assert "storage_order_ambiguous" in topology["diagnostics"]
    assert "storage_capacity_overflow" in topology["diagnostics"]
