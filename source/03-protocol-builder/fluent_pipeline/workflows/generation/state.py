"""Typed state shared by synchronous generation workflow stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...generation_options import GenerationOptions
    from ...progress import ProgressEmitter
    from ...project_context import ProjectLike
    from .workflow import GenerationRequest


@dataclass
class GenerationState:
    """Mutable state passed through one ordered generation-stage sequence."""

    request: GenerationRequest
    generation_options: GenerationOptions
    progress_emitter: ProgressEmitter
    context: ProjectLike | None = None
