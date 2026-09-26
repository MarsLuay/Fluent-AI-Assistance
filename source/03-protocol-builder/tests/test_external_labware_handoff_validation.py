from fluent_pipeline.external_labware_handoff_validation import assess_generated_handoffs


def test_both_sides_are_accepted_and_fingerprinted():
    report = assess_generated_handoffs([
        {
            "command": "execute_application",
            "external_position": "opaque-nest",
            "logical_labware": {"identity": "plate-a", "location": "Nest:1"},
            "logical_update": {"operation": "SetLocation", "identity": "plate-a", "barcode": "BC1"},
            "barcode": {"value": "BC1"},
            "provenance": {"source": "imported_pattern", "evidence": ["source-script"]},
            "injected_outcome": "success",
        }
    ])
    assert report["status"] == "accepted"
    assert report["simulation"]["handoffs"][0]["logical_reconciliation"]["reconciled"] is True
    assert len(report["fingerprint"]) == 64
    again = assess_generated_handoffs([
        {
            "command": "execute_application",
            "external_position": "opaque-nest",
            "logical_labware": {"identity": "plate-a", "location": "Nest:1"},
            "logical_update": {"operation": "SetLocation", "identity": "plate-a", "barcode": "BC1"},
            "barcode": {"value": "BC1"},
            "provenance": {"source": "imported_pattern", "evidence": ["source-script"]},
            "injected_outcome": "success",
        }
    ])
    assert report["fingerprint"] == again["fingerprint"]


def test_missing_or_failed_outcome_cannot_claim_reconciled():
    command = {
        "command": "execute_application",
        "external_position": "opaque-nest",
        "logical_labware": {"identity": "plate-a", "location": "Nest:1"},
        "logical_update": {"operation": "SetLocation", "identity": "plate-a", "barcode": "BC1"},
        "barcode": {"value": "BC1"},
        "provenance": {"source": "imported_pattern", "evidence": ["source-script"]},
    }
    missing = assess_generated_handoffs([command])
    assert missing["status"] == "review"
    assert missing["simulation"]["handoffs"][0]["injected_outcome"] == "unknown"
    assert missing["simulation"]["handoffs"][0]["logical_reconciliation"]["reconciled"] is False
    assert missing["findings"][-1]["code"] == "handoff_not_reconciled"

    failed = assess_generated_handoffs([{**command, "injected_outcome": "failure"}])
    assert failed["status"] == "review"
    assert failed["simulation"]["handoffs"][0]["logical_reconciliation"]["reconciled"] is False
    assert failed["findings"][-1]["code"] == "handoff_not_reconciled"


def test_unknown_external_command_then_logical_mutation_is_review():
    report = assess_generated_handoffs([
        {"command": "execute_application"},
        {"command": "SetLocation", "logical_labware": {"identity": "plate-a", "location": "Nest:1"}},
    ])
    assert report["status"] == "review"
    assert report["findings"][0]["code"] == "unconditional_logical_mutation"
    assert report["simulation"] is None


def test_one_sided_boundary_is_review():
    report = assess_generated_handoffs([
        {
            "command": "execute_application",
            "external_position": "opaque-nest",
            "provenance": {"source": "user_intent", "evidence": ["only-external"]},
        }
    ])
    assert report["status"] == "review"
    assert report["findings"][0]["code"] == "one_sided_boundary"
