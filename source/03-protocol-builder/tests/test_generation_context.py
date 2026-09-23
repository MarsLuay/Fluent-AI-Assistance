from __future__ import annotations

import json

import pytest

from fluent_pipeline.generation_context import (
    GENERATION_CONTEXT_SCHEMA_VERSION,
    GenerationContextValidationError,
    build_generation_context,
    extract_task_facets,
    generation_context_json_schema,
    normalize_generation_context,
    validate_generation_context,
)


def _request(intent: str = "Aspirate reagent and dispense into destination plate") -> dict:
    return {
        "schema_version": "tecan.request_spec.v1",
        "request": {"intent": intent, "created_at": "2026-01-01T00:00:00+00:00"},
        "source": {
            "source_scripts": ["source.xscr"],
            "pattern_index": {"pattern_ids": [7]},
        },
        "verification_recipe": {
            "steps": [
                {"type": "aspirate", "target_labware": "SourcePlate", "liquid_class": "Water"},
                {"type": "dispense", "target_labware": "DestinationPlate", "destination_site": "Nest"},
            ]
        },
    }


def _evidence() -> list[dict]:
    return [
        {
            "category": "source_pattern",
            "content": {"pattern_id": 7, "operation": "aspirate"},
            "selection_reason": "explicit pattern reference from the validated request",
            "selection_mode": "explicit",
            "provenance": {"source": "source.xscr", "command_range": [4, 6]},
            "conflict_key": "pattern:7",
        }
    ]


def test_context_is_json_serializable_and_deterministic() -> None:
    first = build_generation_context(
        _request(),
        evidence_items=_evidence(),
        target_provenance={"fluentcontrol_version": "3.8", "source": "imported manifest"},
    )
    second = build_generation_context(
        {**_request(), "request": {**_request()["request"], "created_at": "2027-01-01T00:00:00+00:00"}},
        evidence_items=_evidence(),
        target_provenance={"source": "imported manifest", "fluentcontrol_version": "3.8"},
    )

    assert first["schema_version"] == GENERATION_CONTEXT_SCHEMA_VERSION
    assert first["context_fingerprint"] == second["context_fingerprint"]
    json.dumps(first)
    assert first["accounting"]["evidence_item_count"] == 1
    assert first["evidence_items"][0]["source_fingerprint"]


def test_facets_preserve_known_intent_and_unknown_intent_without_guessing() -> None:
    facets = extract_task_facets(
        {
            "request": {"intent": "Use the orbital mystery capability"},
            "task_facets": {"future_vendor_feature": {"requested": True}},
        }
    )

    assert facets["operation_families"] == []
    assert facets["unknown_intent"] == ["Use the orbital mystery capability"]
    assert facets["unknown_fields"] == {"future_vendor_feature": {"requested": True}}
    assert build_generation_context(
        {"request": {"intent": "Use the orbital mystery capability"}}
    )["status"] == "needs_review"


def test_missing_and_conflicting_evidence_are_rejected() -> None:
    payload = build_generation_context(_request(), evidence_items=_evidence())
    missing_reason = json.loads(json.dumps(payload))
    missing_reason["evidence_items"][0]["selection_reason"] = ""
    assert any(item["code"] == "missing_required_field" for item in validate_generation_context(missing_reason))

    conflicting = json.loads(json.dumps(payload))
    conflicting["evidence_items"].append(
        {
            **conflicting["evidence_items"][0],
            "id": "source_pattern:other",
            "content": {"pattern_id": 8, "operation": "dispense"},
        }
    )
    assert any(item["code"] == "conflicting_evidence" for item in validate_generation_context(conflicting))

    duplicate = json.loads(json.dumps(payload))
    duplicate["evidence_items"].append(dict(duplicate["evidence_items"][0]))
    assert any(item["code"] == "duplicate_evidence_id" for item in validate_generation_context(duplicate))

    with pytest.raises(GenerationContextValidationError):
        build_generation_context(
            _request(),
            evidence_items=[
                {"category": "source_pattern", "content": {}, "selection_mode": "explicit", "provenance": {}}
            ],
        )


def test_unknown_facet_fields_are_preserved_without_becoming_capabilities() -> None:
    payload = build_generation_context(
        {
            **_request(),
            "task_facets": {"future_contract_field": {"enabled": True}},
        },
        evidence_items=_evidence(),
    )
    assert payload["task_facets"]["unknown_fields"] == {
        "future_contract_field": {"enabled": True}
    }
    assert validate_generation_context(payload) == []


def test_schema_is_explicit_and_unknown_top_level_fields_are_preserved() -> None:
    schema = generation_context_json_schema()
    assert schema["properties"]["schema_version"]["const"] == GENERATION_CONTEXT_SCHEMA_VERSION
    assert "evidence" in schema["$defs"]

    payload = build_generation_context(_request(), evidence_items=_evidence())
    payload["future_contract_field"] = {"enabled": True}
    normalized = normalize_generation_context(payload)
    assert normalized["extensions"]["unknown_fields"] == {
        "future_contract_field": {"enabled": True}
    }
    assert validate_generation_context(normalized) == []
