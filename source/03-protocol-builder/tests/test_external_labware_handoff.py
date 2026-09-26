from fluent_pipeline.external_labware_handoff import (
    build_external_labware_handoff,
    derive_handoff_candidates,
)


def _valid(**overrides):
    record = {
        "device_ref": {"owner": "device", "id": "device-1"},
        "invocation_ref": {"owner": "process", "id": "invoke-1"},
        "direction": "to_external",
        "external_position": "opaque-nest",
        "logical_labware": {"identity": "plate-a", "location": "NestPlatform:1"},
        "sync_mode": "after_named_join",
        "effect": "stated",
        "expected_state_transitions": [{"from": "on_deck", "to": "at_external"}],
        "provenance": {"source": "command_role", "evidence": ["role=handoff"]},
        "unknown_fields": {"vendor_note": "kept"},
    }
    record.update(overrides)
    return record


def test_valid_contract_keeps_identities_distinct_and_json_safe():
    contract = build_external_labware_handoff(_valid())
    assert contract["status"] == "valid"
    assert contract["schema_version"] == "tecan.external_labware_handoff.v1"
    assert contract["external_position"] != contract["logical_labware"]["location"]
    assert contract["logical_labware"]["identity"] == "plate-a"
    assert contract["unknown_fields"]["vendor_note"] == "kept"
    assert contract["owners"]["async_subroutine"] == "subroutine_lifecycle"
    assert contract["effect"] == "stated"


def test_unknown_effect_and_adjacency_are_rejected():
    unknown = build_external_labware_handoff(_valid(effect="unknown"))
    assert unknown["status"] == "invalid"
    assert unknown["expected_state_transitions"] == []
    assert "unknown command effect cannot carry expected state transitions" in unknown["errors"]

    adjacent = build_external_labware_handoff(_valid(provenance={"source": "adjacency", "evidence": []}, effect="unknown", expected_state_transitions=[]))
    assert adjacent["status"] == "invalid"
    assert "adjacency is not handoff evidence" in adjacent["errors"]


def test_candidates_ignore_adjacent_moves():
    commands = [
        {"command": "execute_application", "provenance": {"source": "wrapper_metadata", "evidence": ["explicit"]}},
        {"command": "move_labware"},
        {"command": "execute_application", "provenance": {"source": "adjacency"}},
    ]
    candidates = derive_handoff_candidates(commands)
    assert len(candidates) == 1
    assert candidates[0]["provenance"]["source"] == "wrapper_metadata"
    assert candidates[0]["effect"] == "unknown"
