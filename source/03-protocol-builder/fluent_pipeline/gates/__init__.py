"""Static readiness-gate evaluator registry.

The registry is intentionally explicit.  Readiness must not change because a
plugin happened to be installed or an importable module was discovered.
"""

from .registry import (
    RegisteredGateEvaluator,
    ValidationContext,
    readiness_evaluator,
    readiness_evaluator_registry,
)

__all__ = (
    "RegisteredGateEvaluator",
    "ValidationContext",
    "readiness_evaluator",
    "readiness_evaluator_registry",
)
