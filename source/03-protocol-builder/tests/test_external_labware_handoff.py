"""Focused tests for evidence-backed external labware handoffs."""

from __future__ import annotations

import json

import pytest

from fluent_pipeline.external_labware_handoff import (
    HANDOFF_DIAGNOSTIC_IDS,
    ExternalLabwareHandoff,
    HandoffProvenance,
    derive_handoff_candidates,
    fingerprint_handoff,
    make_handoff_contract,
    validate_handoff_contract,
)


def _provenance(kind: str = "source_command_contract") -> HandoffProvenance:
    return HandoffProvenance(
        kind=kind,
        source_ref="dcc_fetch_001",
        reason="Imported source mapping links the device fetch to the AddLabware step.",
    )


def _contract(**overrides):
    values = {
        "handoff_id": "handoff:fetch-001->add-002",
        "direction": "device_to_deck",
        "external": {
            "integration_ref": "dfw:device-a",
            "device_ref": "device-a",
            "invocation_ref": "dcc_fetch_001",
            "position": {"kind": "opaque", "value": "tower-slot-4", "source_ref": "dcc_fetch_001"},
            "identity": {"barcode": "ABC123"},
        },
        "logical": {
            "step_ref": "add-002",
            "labware_ref": "PlateA",
            "label": "PlateA",
            "catalog": "source-backed-catalog",
            "location": {"site": "TransferStation", "position": 1},
            "identity_expression": "FetchedBarcode",
        },
        "synchronization": {
            "mode": "synchronous",
            "confirmation_ref": "return_code:FetchedBarcode",
            "success_evidence": {"source_ref": "wrapper-result-001"},
        },
        "logical_mutation": {"kind": "add", "step_ref": "add-002", "guard_ref": "fetch-success"},
        "expected_transitions": [
            {"from": "planned", "to": "external_action_started"},
            {"from": "external_action_started", "to": "external_action_succeeded"},
            {"from": "external_action_succeeded", "to": "logical_state_updated"},
            {"from": "logical_state_updated", "to": "reconciled"},
        ],
        "provenance": [_provenance().as_dict()],
    }
    values.update(overrides)
    return values


def test_contract_is_json_serializable_and_fingerprinted_deterministically():
    contract = make_handoff_contract(
        handoff_id="handoff-1",
        direction="device_to_deck",
        external={
            "device_ref": "device-a",
            "invocation_ref": "dcc-1",
            "position": {"kind": "opaque", "value": "slot-1", "source_ref": "dcc-1"},
        },
        logical={"step_ref": "step-2", "labware_ref": "PlateA", "location": {"site": "TransferStation"}},
        synchronization={"mode": "asynchronous_with_confirmation", "confirmation_ref": "wait-3"},
        logical_mutation={"kind": "add", "step_ref": "step-2"},
        expected_transitions=[
            {"from": "planned", "to": "external_action_started"},
            {"from": "external_action_started", "to": "physical_presence_unconfirmed"},
        ],
        provenance=[_provenance()],
        unknown_fields={"vendor_extension": {"raw": "preserved"}},
    )
    encoded = json.dumps(contract.as_dict(), sort_keys=True)
    assert json.loads(encoded)["schema_version"] == "tecan.external_labware_handoff.v1"
    assert contract.fingerprint == fingerprint_handoff(contract)
    assert ExternalLabwareHandoff.from_mapping(contract.as_dict()).fingerprint == contract.fingerprint


def test_validation_requires_explicit_provenance_and_opaque_position():
    missing_provenance = validate_handoff_contract({**_contract(), "provenance": []})
    assert not missing_provenance["ok"]
    assert any(item["id"] == HANDOFF_DIAGNOSTIC_IDS["provenance"] for item in missing_provenance["errors"])

    non_opaque = _contract()
    non_opaque["external"] = {**non_opaque["external"], "position": "slot-4"}
    invalid_position = validate_handoff_contract(non_opaque)
    assert not invalid_position["ok"]
    assert any("opaque" in item["message"] for item in invalid_position["errors"])


def test_unknown_fields_are_preserved_and_fingerprint_detects_tampering():
    contract = ExternalLabwareHandoff.from_mapping({**_contract(), "future_field": {"v": 1}})
    assert contract.unknown_fields["future_field"] == {"v": 1}
    encoded = contract.as_dict()
    encoded["logical"]["label"] = "DifferentPlate"
    checked = validate_handoff_contract(encoded)
    assert not checked["ok"]
    assert any(item["id"] == HANDOFF_DIAGNOSTIC_IDS["fingerprint"] for item in checked["errors"])


def test_candidate_requires_source_mapping_and_does_not_use_adjacency():
    external = [{"contract_id": "dcc-fetch", "module_name": "DeviceModule", "macro_name": "Fetch"}]
    logical = [{"step_id": "step-add", "operation": "add_labware", "label": "PlateA"}]
    report = derive_handoff_candidates(external, logical)
    assert report["candidates"] == []
    assert report["unknown_external_effects"] == ["dcc-fetch"]
    assert any(item["id"] == HANDOFF_DIAGNOSTIC_IDS["untrusted_effect"] for item in report["diagnostics"])
    assert any(item["id"] == HANDOFF_DIAGNOSTIC_IDS["unlinked_mutation"] for item in report["diagnostics"])


def test_candidate_uses_explicit_mapping_and_keeps_external_logical_identities_distinct():
    report = derive_handoff_candidates(
        [{"contract_id": "dcc-fetch", "module_name": "DeviceModule", "device_ref": "device-a"}],
        [{"step_id": "step-add", "operation": "add_labware", "label": "PlateA", "location": {"site": "TransferStation"}}],
        explicit_mappings=[
            {
                "external_ref": "dcc-fetch",
                "logical_step_ref": "step-add",
                "direction": "device_to_deck",
                "sync_mode": "synchronous",
                "external_position": {"kind": "opaque", "value": "slot-1", "source_ref": "device-a"},
                "source_kind": "explicit_user_intent",
                "source_ref": "user-map-1",
                "reason": "User-provided mapping for the verified wrapper pattern.",
            }
        ],
    )
    assert report["status"] == "ready"
    candidate = report["candidates"][0]
    assert candidate["external"]["invocation_ref"] == "dcc-fetch"
    assert candidate["logical"]["step_ref"] == "step-add"
    assert candidate["external"] is not candidate["logical"]
    assert report["unknown_external_effects"] == []


def test_unsupported_external_effect_stays_unknown_even_with_adjacent_add():
    report = derive_handoff_candidates(
        [{"contract_id": "execute-1", "application": "helper.exe"}],
        [{"step_id": "add-1", "type": "add_labware", "label": "PlateA"}],
    )
    assert report["candidates"] == []
    assert report["diagnostics"][0]["id"] in {
        HANDOFF_DIAGNOSTIC_IDS["unlinked_mutation"],
        HANDOFF_DIAGNOSTIC_IDS["untrusted_effect"],
    }
