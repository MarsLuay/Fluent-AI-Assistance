from __future__ import annotations

from fluent_pipeline.api_v2_stepped_inventory import map_ir_steps_to_commands
from fluent_pipeline.api_v2.commands import TransferLabware, command_to_xml
from fluent_pipeline.api_v2_transfer_labware_validate import validate_transfer_labware_offline
from fluent_pipeline.rga_transfer import (
    attach_rga_assessments_to_ir,
    attach_rga_simulation_metadata,
    assess_rga_transfer_step,
)


def _route_input(*, ambiguous: bool = False) -> dict:
    vectors = ["vector-1", "vector-2"] if ambiguous else ["vector-1"]
    return {
        "source_site": {
            "guid": "source-site",
            "allowed_vector_ids": vectors,
            "allowed_vector_evidence": "present",
            "allowed_grip_modes": ["wide"] if not ambiguous else ["wide", "narrow"],
        },
        "destination_site": {
            "guid": "destination-site",
            "allowed_vector_ids": vectors,
            "allowed_vector_evidence": "present",
            "allowed_grip_modes": ["wide"] if not ambiguous else ["wide", "narrow"],
        },
        "catalog": {
            "fingerprint": "catalog-1",
            "vectors": [
                {"id": "vector-1", "end_position": {"x": 1, "y": 2, "z": 3}},
                {"id": "vector-2", "end_position": {"x": 4, "y": 5, "z": 6}},
            ],
        },
    }


def _step(*, ambiguous: bool = False) -> dict:
    return {
        "id": "move-1",
        "operation": "move_plate",
        "target_labware": "Plate1",
        "parameters": {
            "labware": "Plate1",
            "source_location": "SourceCarrier",
            "destination_location": "DestinationCarrier",
            "destination_site": 2,
            "rga_route_input": _route_input(ambiguous=ambiguous),
            "rga_topology_input": {
                "source_site_id": "source-site",
                "destination_site_id": "destination-site",
                "storage_sites": [
                    {"guid": "source-site", "order": 0},
                    {"guid": "destination-site", "order": 1},
                ],
            },
        },
    }


def test_assessment_combines_route_topology_and_logical_only_limits():
    assessment = assess_rga_transfer_step(_step())

    assert assessment["status"] == "ready"
    assert assessment["route_assessment"]["status"] == "direct_supported_by_source"
    assert assessment["route_assessment"]["selected_route"]["vector_id"] == "vector-1"
    assert assessment["topology_transition"]["status"] == "logical_transition_supported_by_source"
    assert assessment["logical_occupancy"]["status"] == "logical_only"
    assert assessment["physical_limitations"]
    assert assessment["source_dependencies"]["route_catalog_fingerprint"] == "catalog-1"


def test_ambiguous_route_is_needs_review_and_stepped_validation_fails_closed():
    step = _step(ambiguous=True)
    assessment = assess_rga_transfer_step(step)
    assert assessment["status"] == "needs_review"
    assert "route_selection_ambiguous" in assessment["review_reasons"]

    ir = {"steps": [step]}
    commands = map_ir_steps_to_commands(ir)
    result = validate_transfer_labware_offline(
        commands[0],
        deck_labels={"plate1"},
        deck_slots={"plate1": ("SourceCarrier", "1")},
    )
    assert not result.ok
    assert result.reason == "rga_route_review_required"


def test_generation_attachment_and_simulation_metadata_preserve_contract_boundary():
    ir, report = attach_rga_assessments_to_ir({"steps": [_step()]})
    params = ir["steps"][0]["parameters"]
    assert report["status"] == "passed"
    assert params["rga_assessment"]["status"] == "ready"
    assert "rga_route_assessment" in params
    assert "rga_topology_transition" in params

    simulation = attach_rga_simulation_metadata({"status": "passed", "warnings": []}, ir)
    transfer = simulation["rga_transfers"][0]
    assert transfer["route"]["status"] == "direct_supported_by_source"
    assert transfer["topology_transition"]["status"] == "logical_transition_supported_by_source"
    assert transfer["logical_occupancy"]["status"] == "logical_only"
    assert transfer["physical_limitations"]


def test_transfer_xml_is_unchanged_when_rga_metadata_is_attached():
    assessment = assess_rga_transfer_step(_step())
    plain = TransferLabware(
        labware="Plate1",
        location="DestinationCarrier",
        site=2,
        module_name="RGA 1",
    )
    enriched = TransferLabware(
        labware="Plate1",
        location="DestinationCarrier",
        site=2,
        module_name="RGA 1",
        rga_route_assessment=assessment["route_assessment"],
        rga_topology_transition=assessment["topology_transition"],
    )

    plain_xml = command_to_xml(plain)
    enriched_xml = command_to_xml(enriched)

    assert enriched_xml == plain_xml
    assert "rga_route_assessment" not in enriched_xml
    assert "rga_topology_transition" not in enriched_xml


def test_ambiguous_simulation_metadata_reports_review_and_physical_limits():
    ir, report = attach_rga_assessments_to_ir({"steps": [_step(ambiguous=True)]})
    simulation = attach_rga_simulation_metadata({"status": "passed", "warnings": []}, ir)

    assert report["status"] == "needs_review"
    transfer = simulation["rga_transfers"][0]
    assert transfer["needs_review"] is True
    assert "route_selection_ambiguous" in transfer["review_reasons"]
    assert {item["effect"] for item in transfer["physical_limitations"]} >= {
        "pathfinder_collision_proof",
        "gripper_clearance_and_retention",
    }
    assert any("RGA transfer step 1 needs review" in warning for warning in simulation["warnings"])


def test_missing_required_route_evidence_is_reviewable_without_inventing_vectors():
    step = {
        "operation": "move_plate",
        "parameters": {"labware": "Plate1", "rga_require_evidence": True},
    }
    assessment = assess_rga_transfer_step(step)
    assert assessment["status"] == "needs_review"
    assert assessment["review_reasons"] == ["route_evidence_missing"]
    assert assessment["route_assessment"] is None
