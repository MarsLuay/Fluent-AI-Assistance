"""Runtime session lifecycle helpers (api-v2-080..082, api-v2-085)."""

from __future__ import annotations

from typing import Any, Mapping


def dispose_concrete_runtime_controller(
    controller: Any | None,
    *,
    via_concrete_type: bool = False,
) -> dict[str, Any]:
    """api-v2-080: dispose only when holding a concrete ``RuntimeController``.

    Normal providers receive ``IRuntimeController`` from ``GetRuntime()`` and
    should rely on ``FluentControl.Dispose()`` instead. This helper is a no-op
    scaffold unless ``via_concrete_type`` is true and ``Dispose`` exists.
    """
    if controller is None:
        return {"disposed": False, "reason": "no_controller"}
    if not via_concrete_type:
        return {
            "disposed": False,
            "reason": "interface_only",
            "note": "IRuntimeController has no Dispose(); use FluentControl.Dispose().",
        }
    dispose = getattr(controller, "Dispose", None)
    if not callable(dispose):
        return {"disposed": False, "reason": "dispose_not_available"}
    try:
        dispose()
    except Exception as exc:
        return {"disposed": False, "reason": "dispose_failed", "error": str(exc)}
    return {"disposed": True, "reason": "concrete_dispose"}


def partition_method_inventory(
    runnable_methods: list[str] | tuple[str, ...] | None,
    maintenance_methods: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """api-v2-081: separate assay runnable names from maintenance-only names."""
    runnable = [str(item).strip() for item in (runnable_methods or []) if str(item).strip()]
    maintenance = [str(item).strip() for item in (maintenance_methods or []) if str(item).strip()]
    maintenance_set = {name.casefold() for name in maintenance}
    assay = [name for name in runnable if name.casefold() not in maintenance_set]
    maintenance_only = [name for name in maintenance if name.casefold() not in {n.casefold() for n in runnable}]
    return {
        "runnable_count": len(runnable),
        "maintenance_count": len(maintenance),
        "assay_runnable": assay,
        "maintenance_methods": maintenance,
        "maintenance_only": maintenance_only,
    }


def record_session_identity(
    *,
    configured_username: str | None,
    observed_username: str | None = None,
) -> dict[str, Any]:
    """api-v2-082: session identity block for runtime-report JSON."""
    configured = str(configured_username or "").strip()
    observed = str(observed_username or "").strip()
    login_verified = bool(configured and observed and configured.casefold() == observed.casefold())
    return {
        "configured_username": configured or None,
        "current_username": observed or None,
        "login_verified": login_verified,
    }


def resume_run_policy(*, enabled: bool, ack_path: str | None = None) -> dict[str, Any]:
    """api-v2-085: opt-in semi-automated ResumeRun after operator prompts."""
    return {
        "resume_run_after_prompts": bool(enabled),
        "default": False,
        "operator_ack_path": ack_path or None,
        "ack_env": "TECAN_OPERATOR_ACK_FILE",
        "ack_flag_env": "TECAN_OPERATOR_ACK",
        "enable_env": "TECAN_RESUME_RUN_AFTER_PROMPTS",
        "safety": {
            "manual_default": True,
            "method_recovery": (
                "ResumeRun advances a paused live/simulation run; confirm deck and "
                "instrument state before acking. Use Method Recovery only when FC "
                "reports a recoverable pause, not after Active Stop or hardware fault."
            ),
            "active_stop": (
                "Never auto-resume after Active Stop, StopMethod, or error dialogs "
                "without operator review; semi-automation applies only to expected "
                "UserPrompt / PauseMode boundaries."
            ),
        },
        "note": (
            "Manual ResumeRun remains the default for verification scripts. "
            "When enabled, a native provider listens for UserPrompt/pause events "
            "and calls ResumeRun only after an external operator-ack signal."
        ),
    }


def validate_method_in_inventory(
    method: str,
    *,
    runnable_methods: list[str] | tuple[str, ...] | None,
    maintenance_methods: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Return errors when *method* is absent from runnable + maintenance inventories (api-v2-081)."""
    target = str(method or "").strip()
    if not target:
        return []
    inventory = partition_method_inventory(runnable_methods, maintenance_methods)
    known = {
        *(name.casefold() for name in inventory["assay_runnable"]),
        *(name.casefold() for name in inventory["maintenance_methods"]),
        *(name.casefold() for name in inventory["maintenance_only"]),
    }
    if not known:
        return []
    if target.casefold() not in known:
        return [
            "Method "
            f"{target!r} was not reported by GetAllRunnableMethods() or GetAllMaintenanceMethods()."
        ]
    return []


def session_identity_errors(session: Mapping[str, Any] | None) -> list[str]:
    """Fail when TECAN_FLUENT_USERNAME is set but GetCurrentUserName does not match (api-v2-082)."""
    if not isinstance(session, Mapping):
        return []
    configured = str(session.get("configured_username") or "").strip()
    if not configured:
        return []
    if session.get("login_verified"):
        return []
    observed = str(session.get("current_username") or "").strip() or "(none)"
    return [
        f"Configured username {configured!r} but GetCurrentUserName() returned {observed!r}."
    ]
