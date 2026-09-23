from __future__ import annotations

import copy

from fluent_pipeline.pattern_index import (
    PATTERN_SELECTION_SCHEMA_VERSION,
    rank_pattern_windows,
)


def _window(
    pattern_id: int,
    pattern_type: str,
    *,
    name: str | None = None,
    confidence: float = 0.95,
    source_script: str = "SourceScript",
    specifications: dict | None = None,
    retrieval: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": pattern_id,
        "pattern_type": pattern_type,
        "name": name or f"{pattern_type} window",
        "source_script": source_script,
        "source_path": f"Scripts/{source_script}.xscr",
        "zeia_file": "source.zeia",
        "start_command_index": 2,
        "end_command_index": 4,
        "step_count": 1,
        "command_signature": pattern_type,
        "confidence": confidence,
        "specifications": specifications or {},
        "safety_notes": ["source-backed"],
        "metadata": metadata or {},
        "retrieval": retrieval or {},
        "steps": [{"command_name": pattern_type, "command_family": pattern_type, "summary": name or pattern_type}],
        "raw_xscr": "must not be copied into the compact candidate",
    }


def test_exact_operation_family_outranks_unrelated_substring_match() -> None:
    result = rank_pattern_windows(
        [
            _window(1, "dispense", name="Aspirate-like label but dispense semantics"),
            _window(2, "aspirate"),
        ],
        {"operation_families": ["aspirate"]},
    )

    assert result["schema_version"] == PATTERN_SELECTION_SCHEMA_VERSION
    assert result["selected"][0]["pattern"]["id"] == 2
    assert "raw_xscr" not in result["selected"][0]["pattern"]
    assert result["selected"][0]["score_breakdown"]["operation_family"] == 100


def test_device_labware_and_liquid_evidence_improves_relevant_candidate() -> None:
    result = rank_pattern_windows(
        [
            _window(1, "aspirate", specifications={"labware": ["OtherPlate"], "liquid_classes": ["Other"]}),
            _window(
                2,
                "aspirate",
                specifications={
                    "labware": ["SourcePlate"],
                    "liquid_classes": ["Water"],
                    "device_aliases": ["MCA384"],
                },
            ),
        ],
        {
            "operation_families": ["aspirate"],
            "labware_names": ["SourcePlate"],
            "liquid_roles": ["Water"],
            "device_families": ["MCA384"],
        },
    )

    assert result["selected"][0]["pattern"]["id"] == 2
    assert set(result["selected"][0]["matched_facets"]) >= {"labware", "liquid_class", "device_family"}


def test_incompatible_and_low_confidence_candidates_are_not_selected() -> None:
    result = rank_pattern_windows(
        [
            _window(1, "aspirate", confidence=0.99),
            _window(2, "aspirate", confidence=0.99, metadata={"supported_target_versions": ["3.8"]}),
            _window(3, "aspirate", confidence=0.2),
            _window(4, "aspirate", confidence=0.99, metadata={"supported_target_versions": ["3.7"]}),
        ],
        {"operation_families": ["aspirate"]},
        target_evidence={
            "incompatible_pattern_ids": [1],
            "target_fluentcontrol_version": "3.8",
        },
    )

    assert result["status"] == "ready"
    assert result["selected"][0]["pattern"]["id"] == 2
    omitted = {item["candidate_id"]: item["reason"] for item in result["omissions"]}
    assert omitted["pattern:1"] == "incompatible_target_evidence"
    assert omitted["pattern:3"] == "low_confidence"
    assert omitted["pattern:4"] == "incompatible_target_version"


def test_ambiguous_candidates_remain_visible_and_explicit_selection_wins() -> None:
    windows = [_window(1, "aspirate"), _window(2, "aspirate")]
    ambiguous = rank_pattern_windows(windows, {"operation_families": ["aspirate"]})
    assert ambiguous["status"] == "needs_review"
    assert ambiguous["selected"] == []
    assert [item["pattern"]["id"] for item in ambiguous["alternatives"]] == [1, 2]

    explicit = rank_pattern_windows(
        windows,
        {"operation_families": ["aspirate"]},
        explicit_pattern_ids=[2],
    )
    assert explicit["status"] == "ready"
    assert [item["pattern"]["id"] for item in explicit["selected"]] == [2]
    assert explicit["selection_mode"] == "explicit"


def test_ranking_is_repeatable_and_preserves_explicit_query_and_script_overrides() -> None:
    windows = [
        _window(1, "aspirate", source_script="A", retrieval={"method": "pattern_query", "query": "aspirate"}),
        _window(2, "dispense", source_script="B"),
    ]
    first = rank_pattern_windows(
        windows,
        {"operation_families": ["dispense"]},
        explicit_pattern_queries=["aspirate"],
    )
    second = rank_pattern_windows(
        copy.deepcopy(windows),
        {"operation_families": ["dispense"]},
        explicit_pattern_queries=["aspirate"],
    )
    assert first["fingerprint"] == second["fingerprint"]
    assert first["selected"][0]["pattern"]["id"] == 1
    assert first["selected"][0]["selection_reason"] == "explicit source/pattern selection"
