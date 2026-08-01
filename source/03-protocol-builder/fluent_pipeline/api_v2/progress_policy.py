"""Progress delegate policy (api-v2-077, api-v2-078)."""

from __future__ import annotations


class ProgressSyncPolicy:
    """Workflow tooling must not scatter ``Progress.BeginInvoke`` / ``EndInvoke``."""

    ALLOW_ASYNC_INVOKE = False
    PREFERRED_SOURCES = ("ProgressChanged", "GetProgress", "GetProgressInitialization")


def progress_wait_guidance() -> str:
    """Human-readable guidance recorded in runtime-report details."""
    return (
        "Use synchronous IRuntimeControllerEvents.ProgressChanged or poll "
        "GetProgress()/GetProgressInitialization(); do not call Progress.BeginInvoke "
        "or manual EndInvoke pairing in Gate 27 / generate --event-log integrators."
    )
