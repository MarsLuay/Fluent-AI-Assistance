"""Canonical generation workflow stage implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from ...project_context import ProjectLike
from .state import GenerationState


ContextLoader = Callable[[str | None], ProjectLike | None]
ContextSummary = Callable[[ProjectLike | None], str]


@dataclass(frozen=True)
class LoadContextStage:
    """Load the requested project context with the legacy progress contract."""

    load_context: ContextLoader
    summarize_context: ContextSummary

    stage_id: ClassVar[str] = "load_context"

    def run(self, state: GenerationState) -> None:
        state.progress_emitter.started(self.stage_id)
        try:
            state.context = self.load_context(state.request.context_name)
        except Exception as exc:
            state.progress_emitter.failed(self.stage_id, str(exc))
            raise
        state.progress_emitter.completed(self.stage_id, self.summarize_context(state.context))
