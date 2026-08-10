from __future__ import annotations

from dataclasses import dataclass

import pytest

from fluent_pipeline.progress import GENERATION_PROGRESS_STAGES, ProgressEmitter
from fluent_pipeline.runner import PipelineError
import fluent_pipeline.generation_workflow as generation_facade
from fluent_pipeline.workflows.generation import (
    GenerationStageRunner,
    GenerationState,
    LoadContextStage,
    run_generation_workflow as canonical_run_generation_workflow,
)


@dataclass(frozen=True)
class _Request:
    context_name: str | None


@dataclass
class _MarkerStage:
    stage_id: str
    marker: str

    def run(self, state: GenerationState) -> None:
        state.context = f"{state.context or ''}{self.marker}"


def _state(*, context_name: str | None = None, callback=None) -> GenerationState:
    return GenerationState(
        request=_Request(context_name),  # type: ignore[arg-type]
        generation_options=object(),  # type: ignore[arg-type]
        progress_emitter=ProgressEmitter(GENERATION_PROGRESS_STAGES, callback),
    )


def test_stage_runner_is_sequential_and_preserves_one_shared_state() -> None:
    state = _state()

    result = GenerationStageRunner(
        (_MarkerStage("first", "a"), _MarkerStage("second", "b"))
    ).run(state)

    assert result is state
    assert state.context == "ab"


def test_generation_workflow_implementation_is_owned_by_generation_package() -> None:
    assert canonical_run_generation_workflow.__module__ == (
        "fluent_pipeline.workflows.generation.workflow"
    )
    assert generation_facade.GenerationRequest.__module__ == (
        "fluent_pipeline.workflows.generation.workflow"
    )


def test_load_context_stage_preserves_legacy_progress_contract() -> None:
    events = []
    calls = []
    state = _state(context_name="example", callback=events.append)
    context = object()

    LoadContextStage(
        load_context=lambda name: calls.append(name) or context,  # type: ignore[arg-type]
        summarize_context=lambda loaded: "Loaded example context" if loaded is context else "wrong",
    ).run(state)

    assert calls == ["example"]
    assert state.context is context
    assert [(event.stage_id, event.status, event.message) for event in events] == [
        ("load_context", "started", None),
        ("load_context", "completed", "Loaded example context"),
    ]


def test_load_context_stage_reports_failure_and_reraises() -> None:
    events = []
    state = _state(context_name="broken", callback=events.append)

    with pytest.raises(PipelineError, match="context unavailable"):
        LoadContextStage(
            load_context=lambda _name: (_ for _ in ()).throw(PipelineError("context unavailable")),
            summarize_context=lambda _context: "unused",
        ).run(state)

    assert [(event.stage_id, event.status, event.message) for event in events] == [
        ("load_context", "started", None),
        ("load_context", "failed", "context unavailable"),
    ]
