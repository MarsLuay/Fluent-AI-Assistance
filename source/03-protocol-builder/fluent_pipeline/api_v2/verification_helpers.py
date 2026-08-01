"""Optional live verification helpers (api-v2-083, api-v2-084)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


ENVIRONMENTAL_DATA_TEMPLATE: dict[str, Any] = {
    "temperature_c": None,
    "humidity_pct": None,
    "pressure_hpa": None,
    "recorded_at": None,
    "sensor_notes": "",
}


@dataclass(frozen=True)
class InteriorLightOptions:
    """Opt-in live interior-light toggle before operator prompts (api-v2-083)."""

    interior_light_before_prompts: bool = False


def interior_light_verification_action(*, on: bool) -> dict[str, Any]:
    """api-v2-083: observe-only InteriorLight helper for teaching/verification runs."""
    registry_command = "InteriorLightOnStatement" if on else "InteriorLightOffStatement"
    return {
        "action": "InteriorLight",
        "on": bool(on),
        "registry_command": registry_command,
        "runtime_method": "RuntimeController.InteriorLight",
        "observe_only": True,
    }


def interior_light_options_from_env() -> InteriorLightOptions:
    """Build interior-light flags from optional ``TECAN_INTERIOR_LIGHT_BEFORE_PROMPTS``."""
    enabled = os.environ.get("TECAN_INTERIOR_LIGHT_BEFORE_PROMPTS", "").strip().casefold() in {
        "1",
        "true",
        "yes",
    }
    return InteriorLightOptions(interior_light_before_prompts=enabled)


def interior_light_policy(*, enabled: bool) -> dict[str, Any]:
    """Policy block for runtime-report JSON (api-v2-083)."""
    return {
        "interior_light_before_prompts": bool(enabled),
        "default": False,
        "enable_env": "TECAN_INTERIOR_LIGHT_BEFORE_PROMPTS",
        "registry_commands": {
            "on": "InteriorLightOnStatement",
            "off": "InteriorLightOffStatement",
        },
        "runtime_method": "RuntimeController.InteriorLight",
        "observe_only_compile": True,
        "note": (
            "Live Gate 27 / verification runs may turn the interior light on before "
            "UserPrompt steps for deck visibility during RGA/A200 walkthroughs. "
            "Offline generate/compile is unchanged."
        ),
    }


def call_interior_light(runtime: Any, *, on: bool) -> tuple[bool, str | None]:
    """Invoke ``RuntimeController.InteriorLight(onOff)`` when available."""
    light = getattr(runtime, "InteriorLight", None) or getattr(runtime, "interior_light", None)
    if light is None:
        return False, "InteriorLight is not available on the runtime controller."
    try:
        light(bool(on))
        return True, None
    except Exception as exc:
        return False, str(exc)


def toggle_interior_light_before_prompt(
    runtime: Any,
    *,
    options: InteriorLightOptions | None = None,
    at_prompt_boundary: bool = False,
    on: bool = True,
) -> dict[str, Any]:
    """Call ``InteriorLight`` before operator prompts when explicitly enabled (api-v2-083).

    Default Gate 27 and offline generation stay unchanged; teaching/verification
    runs opt in via ``InteriorLightOptions.interior_light_before_prompts`` or
    ``TECAN_INTERIOR_LIGHT_BEFORE_PROMPTS``.
    """
    action = interior_light_verification_action(on=on)
    opts = options or InteriorLightOptions()
    if not opts.interior_light_before_prompts or not at_prompt_boundary:
        action.update({"invoked": False, "reason": "disabled"})
        return action
    if runtime is None:
        action.update({"invoked": False, "reason": "no_runtime"})
        return action
    success, error = call_interior_light(runtime, on=on)
    action.update(
        {
            "invoked": True,
            "success": success,
            "reason": "ok" if success else "invoke_failed",
        }
    )
    if error:
        action["error"] = error
    return action


def environmental_pre_run_template(**overrides: Any) -> dict[str, Any]:
    """api-v2-084: template payload for ReportEnvironmentalData pre-run hooks."""
    payload = dict(ENVIRONMENTAL_DATA_TEMPLATE)
    payload.update({key: value for key, value in overrides.items() if key in ENVIRONMENTAL_DATA_TEMPLATE})
    payload["api_method"] = "RuntimeController.ReportEnvironmentalData"
    payload["status"] = "template_only"
    return payload
