from fluent_pipeline.inference import (
    Confidence,
    InferenceCandidate,
    InferenceOrigin,
    apply_inference_decisions,
    build_inference_report,
    infer_value,
    merge_inference_reports,
    rank_candidates,
    records_to_candidates,
    render_inference_markdown,
)


def _candidate(value: str, *, priority: int, score: int = 0) -> InferenceCandidate:
    return InferenceCandidate(
        value=value,
        origin=InferenceOrigin.CONTEXT_ROLE,
        source=f"synthetic:{value}",
        reason="Synthetic role match.",
        priority=priority,
        score=score,
    )


def test_explicit_value_always_wins() -> None:
    decision = infer_value(
        "$.template.parameters.source_plate",
        explicit="Reviewed source",
        candidates=[_candidate("Inferred source", priority=999)],
    )

    assert decision.value == "Reviewed source"
    assert decision.origin is InferenceOrigin.EXPLICIT
    assert decision.confidence is Confidence.HIGH
    assert not decision.review_required


def test_ranking_is_deterministic_and_deduplicated() -> None:
    candidates = [
        _candidate("Beta", priority=100),
        _candidate("Alpha", priority=100),
        _candidate("Alpha", priority=50, score=999),
    ]

    assert [item.value for item in rank_candidates(candidates)] == ["Alpha", "Beta"]
    assert rank_candidates(reversed(candidates)) == rank_candidates(candidates)


def test_tied_fallback_is_low_confidence() -> None:
    decision = infer_value(
        "$.template.parameters.worktable",
        candidates=[_candidate("B", priority=100), _candidate("A", priority=100)],
    )

    assert decision.value == "A"
    assert decision.confidence is Confidence.LOW
    assert decision.review_required


def test_records_rank_intent_match_before_stable_fallback() -> None:
    candidates = records_to_candidates(
        [
            {"object_name": "Synthetic cleanup", "entry": "b"},
            {"object_name": "Synthetic transfer", "entry": "a"},
        ],
        source_prefix="script",
        reason="Match the task to an imported script.",
        intent="transfer a plate",
    )

    decision = infer_value("$.source.source_scripts", candidates=candidates)
    assert decision.value == "Synthetic transfer"


def test_unresolved_decision_is_not_applied() -> None:
    unresolved = infer_value("$.template.parameters.liquid_class")
    report = build_inference_report(
        [unresolved],
        context="synthetic-context",
        task="Synthetic task",
    )

    assert unresolved.unresolved
    assert report["status"] == "degraded"
    assert report["unresolved_paths"] == ["$.template.parameters.liquid_class"]
    assert apply_inference_decisions({"template": {}}, [unresolved]) == {"template": {}}


def test_decisions_apply_to_nested_request_paths() -> None:
    decision = infer_value(
        "$.template.parameters.transfer_volume_ul",
        candidates=[
            InferenceCandidate(
                value=25,
                origin=InferenceOrigin.TEMPLATE_DEFAULT,
                source="template:plate_transfer",
                reason="Reuse the inert template shape.",
                priority=50,
            )
        ],
    )

    resolved = apply_inference_decisions({"template": {"name": "plate_transfer"}}, [decision])
    assert resolved["template"]["parameters"]["transfer_volume_ul"] == 25
    assert decision.confidence is Confidence.LOW


def test_reports_merge_by_path_and_render_for_review() -> None:
    source = infer_value(
        "$.source.source_scripts",
        candidates=[_candidate("Synthetic transfer", priority=100)],
    )
    unresolved = infer_value("$.template.parameters.liquid_class")
    merged = merge_inference_reports(
        build_inference_report([source], context="synthetic", task="transfer"),
        build_inference_report([unresolved], context="synthetic", task="transfer"),
    )

    assert merged["status"] == "degraded"
    assert merged["inferred_count"] == 1
    assert merged["unresolved_count"] == 1
    markdown = render_inference_markdown(merged)
    assert "# Automatic inference" in markdown
    assert "Synthetic transfer" in markdown
    assert "Final-generation boundary" in markdown
