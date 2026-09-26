from fluent_pipeline.external_labware_handoff import build_external_labware_handoff
from fluent_pipeline.external_labware_reconcile import reconcile_external_labware_outcome


def _handoff(**overrides):
    raw = {
        "direction": "to_external",
        "external_position": "opaque-nest",
        "logical_labware": {"identity": "plate-a", "location": "Nest:1"},
        "sync_mode": "synchronous",
        "effect": "stated",
        "expected_state_transitions": [{"from": "on_deck", "to": "at_external"}],
        "provenance": {"source": "command_role", "evidence": ["role=handoff"]},
    }
    raw.update(overrides)
    return build_external_labware_handoff(raw)


def _update(**overrides):
    record = {"operation": "SetLocation", "identity": "plate-a", "barcode": "BC1", "location": "Nest:1"}
    record.update(overrides)
    return record


def test_success_reconciles_only_with_a_source_backed_meaning():
    result = reconcile_external_labware_outcome(
        _handoff(),
        {"meaning": "success", "barcode": {"value": "BC1", "provenance": "wrapper_metadata"}},
        _update(),
    )
    assert result["reconciled"] is True
    assert result["lineage"]["barcode"]["value"] == "BC1"
    assert result["lineage"]["external_position"] == "opaque-nest"

    guessed = reconcile_external_labware_outcome(_handoff(), {"return_code": 0}, _update())
    assert guessed["reconciled"] is False
    assert guessed["findings"][0]["code"] == "outcome_not_source_backed"


def test_failure_unknown_and_async_do_not_reconcile():
    failed = reconcile_external_labware_outcome(
        _handoff(),
        {"meaning": "failure"},
        _update(),
    )
    assert failed["reconciled"] is False
    assert failed["findings"][-1]["category"] == "device_failure"

    unknown = reconcile_external_labware_outcome(_handoff(effect="unknown", expected_state_transitions=[]), {"meaning": "unknown"})
    assert unknown["reconciled"] is False
    assert unknown["findings"][0]["category"] == "physical_uncertainty"

    early = reconcile_external_labware_outcome(
        _handoff(sync_mode="after_named_join"),
        {"meaning": "success", "barcode": {"value": "BC1"}},
        _update(),
        completion_boundary_verified=False,
    )
    assert early["reconciled"] is False
    assert early["findings"][0]["code"] == "async_update_before_confirmation"


def test_barcode_mismatch_stale_occupancy_and_ambiguous_retry():
    mismatch = reconcile_external_labware_outcome(
        _handoff(),
        {"meaning": "success", "barcode": {"value": "BC1"}},
        _update(barcode="BC2"),
    )
    assert {item["code"] for item in mismatch["findings"]} >= {"barcode_mismatch"}
    assert mismatch["findings"][0]["category"] == "logical_inconsistency"

    stale = reconcile_external_labware_outcome(
        _handoff(),
        {"meaning": "success", "barcode": {"value": "BC1"}, "retry": "ambiguous"},
        _update(occupancy="stale"),
    )
    assert {item["code"] for item in stale["findings"]} >= {"stale_occupancy", "ambiguous_retry"}
    assert stale["reconciled"] is False
