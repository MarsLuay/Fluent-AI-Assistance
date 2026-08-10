"""Canonical synchronous generation workflow stages and orchestration."""

from .runner import GenerationStage, GenerationStageRunner
from .stages import LoadContextStage
from .state import GenerationState

_WORKFLOW_EXPORTS = {
    "ApprovalSet",
    "GENERATION_STAGES",
    "GenerationRequest",
    "PROGRESS_HEARTBEAT_SECONDS",
    "run_generation_workflow",
}


def __getattr__(name: str):
    if name not in _WORKFLOW_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import workflow

    value = getattr(workflow, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _WORKFLOW_EXPORTS)

__all__ = [
    "ApprovalSet",
    "GENERATION_STAGES",
    "GenerationStage",
    "GenerationStageRunner",
    "GenerationRequest",
    "GenerationState",
    "LoadContextStage",
    "PROGRESS_HEARTBEAT_SECONDS",
    "run_generation_workflow",
]
