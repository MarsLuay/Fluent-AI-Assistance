from __future__ import annotations

from fluent_pipeline.generation_context import (
    build_generation_context,
    build_repair_delta_context,
    compact_generation_context,
    validate_generation_context,
)


def _context() -> dict:
    return build_generation_context(
        {"request": {"intent": "Aspirate from source plate"}},
        evidence_items=[
            {
                "id": "pattern:7",
                "category": "source_pattern",
                "content": {"pattern_id": 7, "command": "aspirate"},
                "selection_reason": "explicit source pattern",
                "selection_mode": "explicit",
                "provenance": {"source": "source.xscr"},
                "conflict_key": "pattern:7",
            },
            {
                "id": "catalog:unrelated",
                "category": "catalog",
                "content": {"name": "unrelated", "description": "x" * 500},
                "selection_reason": "optional catalog context",
                "selection_mode": "automatic_ranked",
                "provenance": {"source": "catalog.json"},
            },
        ],
    )


def test_small_budget_keeps_atomic_source_pattern_and_omits_redundant_context() -> None:
    compacted = compact_generation_context(_context(), max_bytes=1)

    assert [item["id"] for item in compacted["evidence_items"]] == ["pattern:7"]
    assert any(item["candidate_id"] == "catalog:unrelated" for item in compacted["omissions"])
    assert compacted["extensions"]["compaction"]["over_budget_critical"] is True
    assert validate_generation_context(compacted) == []


def test_character_and_token_budgets_keep_atomic_source_patterns() -> None:
    for limits in ({"max_characters": 1}, {"max_tokens": 1}):
        compacted = compact_generation_context(_context(), **limits)

        assert [item["id"] for item in compacted["evidence_items"]] == ["pattern:7"]
        assert compacted["extensions"]["compaction"]["over_budget_critical"] is True


def test_duplicate_windows_merge_metadata_and_are_accounted_deterministically() -> None:
    context = _context()
    context["evidence_items"].append(
        {
            **context["evidence_items"][0],
            "id": "pattern:7-alternative",
            "extensions": {"candidate_score": 91},
        }
    )
    compacted = compact_generation_context(context)

    pattern_items = [
        item for item in compacted["evidence_items"] if item["category"] == "source_pattern"
    ]
    assert [item["id"] for item in pattern_items] == ["pattern:7"]
    assert pattern_items[0]["extensions"]["duplicate_candidates"][0]["id"] == "pattern:7-alternative"
    assert pattern_items[0]["extensions"]["duplicate_candidates"][0]["extensions"] == {"candidate_score": 91}
    assert compacted["extensions"]["compaction"]["deduplicated_count"] == 1
    assert validate_generation_context(compacted) == []


def test_budget_and_fingerprint_are_repeatable_and_budget_changes_output() -> None:
    context = _context()
    small = compact_generation_context(context, max_bytes=1)
    repeat = compact_generation_context(context, max_bytes=1)
    larger = compact_generation_context(context, max_bytes=10000)

    assert small["context_fingerprint"] == repeat["context_fingerprint"]
    assert small["context_fingerprint"] != larger["context_fingerprint"]
    assert len(larger["evidence_items"]) == 2


def test_repair_delta_keeps_implicated_safe_data_and_drops_full_logs_and_unrelated_catalogs() -> None:
    context = build_repair_delta_context(
        diagnostics=[
            {
                "id": "diag-1",
                "code": "SCRIPT_LOAD_FAILED",
                "severity": "error",
                "message": "source pattern failed",
                "source_ref": "source.xscr",
                "contract_id": "contract-1",
                "full_log": "do not copy",
                "catalog": {"all": "do not copy"},
            }
        ],
        source_lineage=[
            {"lineage_id": "lineage-1", "source_ref": "source.xscr", "command_index": 4},
            {"lineage_id": "unrelated", "source_ref": "other.xscr", "command_index": 9},
        ],
        relevant_contracts=[
            {"contract_id": "contract-1", "name": "Aspirate", "parameters": {"head": "MCA384"}},
            {"contract_id": "unrelated", "name": "Unused", "parameters": {"head": "RGA"}},
        ],
        repair_actions=[
            {"id": "safe-1", "action": "review_source_pattern", "safe": True},
            {"id": "unsafe-1", "action": "run_hardware", "safe": False},
        ],
    )

    categories = {item["category"] for item in context["evidence_items"]}
    assert categories == {"repair_diagnostic", "source_lineage", "repair_contract", "repair_action"}
    serialized = str(context)
    assert "do not copy" not in serialized
    assert "unrelated" not in serialized
    assert validate_generation_context(context) == []
