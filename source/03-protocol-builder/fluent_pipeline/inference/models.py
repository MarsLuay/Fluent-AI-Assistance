"""Serializable inference contracts shared by request and IR resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InferenceOrigin(StrEnum):
    EXPLICIT = "explicit"
    EXACT_SOURCE = "exact_source"
    SOURCE_CONSENSUS = "source_consensus"
    CONTEXT_ROLE = "context_role"
    CONTEXT_FALLBACK = "context_fallback"
    TEMPLATE_DEFAULT = "template_default"
    PROMPT_FALLBACK = "prompt_fallback"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class InferenceCandidate:
    """One possible value and the evidence used to rank it."""

    value: Any
    origin: InferenceOrigin
    source: str
    reason: str
    priority: int
    score: int = 0
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "origin": self.origin.value,
            "source": self.source,
            "reason": self.reason,
            "priority": self.priority,
            "score": self.score,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class InferenceDecision:
    """The selected value for one request/IR path."""

    path: str
    value: Any
    origin: InferenceOrigin
    confidence: Confidence
    source: str
    reason: str
    review_required: bool
    unresolved: bool = False
    candidates: tuple[InferenceCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "value": self.value,
            "origin": self.origin.value,
            "confidence": self.confidence.value,
            "source": self.source,
            "reason": self.reason,
            "review_required": self.review_required,
            "unresolved": self.unresolved,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }
