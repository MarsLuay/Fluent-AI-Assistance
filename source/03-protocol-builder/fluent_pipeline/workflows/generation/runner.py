"""Synchronous stage runner for generation workflow decomposition."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .state import GenerationState


class GenerationStage(Protocol):
    """One ordered, synchronous generation workflow stage."""

    stage_id: str

    def run(self, state: GenerationState) -> None:
        """Mutate shared state or raise without starting concurrent work."""


class GenerationStageRunner:
    """Run stages in declaration order against exactly one shared state object."""

    def __init__(self, stages: Sequence[GenerationStage]) -> None:
        self._stages = tuple(stages)

    def run(self, state: GenerationState) -> GenerationState:
        for stage in self._stages:
            stage.run(state)
        return state
