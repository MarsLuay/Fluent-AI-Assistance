"""Worktable helpers shared by IR annotation and simulator handoff (offline)."""

from __future__ import annotations

from typing import Any, Mapping

EXECUTION_STEPS_KEY = "execution_steps"


def worktable_name_from_ir(ir: Mapping[str, Any] | None) -> str | None:
    if not isinstance(ir, Mapping):
        return None
    worktable = ir.get("worktable")
    if not isinstance(worktable, Mapping):
        return None
    name = str(worktable.get("name") or "").strip()
    return name or None


def worktable_guid_from_ir(ir: Mapping[str, Any] | None) -> str | None:
    if not isinstance(ir, Mapping):
        return None
    worktable = ir.get("worktable")
    if not isinstance(worktable, Mapping):
        return None
    guid = str(worktable.get("guid") or "").strip()
    return guid or None


def initialization_worktable_from_spec(spec: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    if not isinstance(spec, Mapping):
        return None, None
    recipe = spec.get("verification_recipe")
    if not isinstance(recipe, Mapping):
        return None, None
    name = str(recipe.get("initialization_worktable") or "").strip() or None
    guid = str(recipe.get("initialization_worktable_guid") or "").strip() or None
    return name, guid


def execution_steps_from_report(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    steps = report.get(EXECUTION_STEPS_KEY)
    if isinstance(steps, list):
        return [dict(step) for step in steps if isinstance(step, dict)]
    details = report.get("details")
    if isinstance(details, Mapping):
        nested = details.get(EXECUTION_STEPS_KEY)
        if isinstance(nested, list):
            return [dict(step) for step in nested if isinstance(step, dict)]
        command_log = details.get("command_log")
        if isinstance(command_log, list):
            return [dict(step) for step in command_log if isinstance(step, dict)]
    external = report.get("external_json")
    if isinstance(external, Mapping):
        nested = external.get(EXECUTION_STEPS_KEY)
        if isinstance(nested, list):
            return [dict(step) for step in nested if isinstance(step, dict)]
    return []
