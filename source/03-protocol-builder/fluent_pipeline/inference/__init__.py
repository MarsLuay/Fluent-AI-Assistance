"""Deterministic, offline inference for incomplete authoring requests."""

from .engine import (
    apply_inference_decisions,
    build_inference_report,
    infer_value,
    intent_similarity,
    merge_inference_reports,
    rank_candidates,
    records_to_candidates,
    render_inference_markdown,
)
from .models import (
    Confidence,
    InferenceCandidate,
    InferenceDecision,
    InferenceOrigin,
)

__all__ = [
    "Confidence",
    "InferenceCandidate",
    "InferenceDecision",
    "InferenceOrigin",
    "apply_inference_decisions",
    "build_inference_report",
    "infer_value",
    "intent_similarity",
    "merge_inference_reports",
    "rank_candidates",
    "records_to_candidates",
    "render_inference_markdown",
]
