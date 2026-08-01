"""CapBC / ScanTubes verification-recipe checks for ``validate-spec``."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .request_spec import recipe_step_type, recipe_subroutine_name
from .subroutine_deck_locations import (
    GRIPPER_CLOSE_VARIABLE,
    GRIPPER_OPEN_VARIABLE,
    INPUT_SUB_LOCATION_TARGET,
    is_capbc_subroutine,
    mapping_needs_input_sub_location_fix,
    needs_prep_fixup,
    resolve_tube_deck_location,
    subroutine_needs_tube_prep,
)

SCAN_TUBES_SUBROUTINE_PATTERN = re.compile(r"ScanTubes", re.IGNORECASE)


def is_scantubes_subroutine(name: str) -> bool:
    base = str(name or "").strip().rsplit("\\", 1)[-1]
    return bool(SCAN_TUBES_SUBROUTINE_PATTERN.search(base))


def recipe_step_variable_mappings(raw_step: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return ``variable_mappings_start`` from a recipe subroutine step."""
    sub = raw_step.get("subroutine") if isinstance(raw_step.get("subroutine"), dict) else raw_step
    if not isinstance(sub, dict):
        sub = raw_step
    mappings = sub.get("variable_mappings_start")
    if not isinstance(mappings, list):
        mappings = raw_step.get("variable_mappings_start")
    if not isinstance(mappings, list):
        return []
    return [dict(item) for item in mappings if isinstance(item, dict)]


def explicit_gripper_values_from_recipe(recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Gripper values declared directly on the recipe (not mined from source scripts)."""
    values: dict[str, Any] = {}
    prep = recipe.get("capbc_prep")
    if isinstance(prep, dict):
        for name in (GRIPPER_CLOSE_VARIABLE, GRIPPER_OPEN_VARIABLE):
            if name in prep and str(prep.get(name) or "").strip() != "":
                values[name] = prep[name]
    if recipe.get("tube_gripper_close") is not None and str(recipe.get("tube_gripper_close")).strip() != "":
        values[GRIPPER_CLOSE_VARIABLE] = recipe.get("tube_gripper_close")
    if recipe.get("tube_gripper_open") is not None and str(recipe.get("tube_gripper_open")).strip() != "":
        values[GRIPPER_OPEN_VARIABLE] = recipe.get("tube_gripper_open")
    for step in recipe.get("prep_steps") or []:
        if not isinstance(step, dict) or recipe_step_type(step) != "set_variable":
            continue
        payload = step.get("set_variable") if isinstance(step.get("set_variable"), dict) else step
        if not isinstance(payload, dict):
            continue
        variable = str(payload.get("variable") or "").strip()
        if variable in {GRIPPER_CLOSE_VARIABLE, GRIPPER_OPEN_VARIABLE}:
            values[variable] = payload.get("value")
    return values


def iter_recipe_subroutine_steps(recipe: Mapping[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """Yield ``(location, step, subroutine_name)`` for each recipe subroutine call."""
    rows: list[tuple[str, dict[str, Any], str]] = []
    for gi, group in enumerate(recipe.get("groups") or []):
        if not isinstance(group, dict):
            continue
        steps = group.get("steps")
        if not isinstance(steps, list):
            continue
        for si, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            name = recipe_subroutine_name(step)
            if not name:
                continue
            rows.append((f"verification_recipe.groups[{gi}].steps[{si}]", step, name))
    return rows


def lint_capbc_scantubes_recipe(
    spec: Mapping[str, Any],
    recipe: Mapping[str, Any],
    result: Any,
) -> None:
    """Add warnings for CapBC / ScanTubes prep and deck-location gaps."""
    subroutine_steps = iter_recipe_subroutine_steps(recipe)
    tube_prep_steps = [
        (loc, step, name)
        for loc, step, name in subroutine_steps
        if subroutine_needs_tube_prep(name=name, mappings=recipe_step_variable_mappings(step))
    ]
    capbc_steps = [(loc, step, name) for loc, step, name in tube_prep_steps if is_capbc_subroutine(name)]
    scantubes_steps = [(loc, step, name) for loc, step, name in tube_prep_steps if is_scantubes_subroutine(name)]
    # Also include ScanTubes / CapBC by name for lint visibility even when mappings empty.
    if not capbc_steps:
        capbc_steps = [(loc, step, name) for loc, step, name in subroutine_steps if is_capbc_subroutine(name)]
    if not scantubes_steps:
        scantubes_steps = [(loc, step, name) for loc, step, name in subroutine_steps if is_scantubes_subroutine(name)]
    if not tube_prep_steps and not capbc_steps and not scantubes_steps:
        return

    source = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    source_scripts = source.get("source_scripts") if isinstance(source.get("source_scripts"), list) else []
    has_source_script = any(str(item or "").strip() for item in source_scripts)

    if not has_source_script:
        labels = []
        if capbc_steps:
            labels.append("CapBC")
        if scantubes_steps:
            labels.append("ScanTubes")
        joined = "/".join(labels)
        result.add(
            "warning",
            "source.source_scripts",
            f"{joined} subroutine call(s) are declared in the verification recipe but "
            "source.source_scripts is missing or empty, so generation cannot mine "
            "GripperClose/GripperOpen/TubeLocationName prep values from a source "
            "script. Set source.source_scripts, verification_recipe.capbc_prep, or "
            "verification_recipe.prep_steps.",
        )

    if capbc_steps:
        _lint_capbc_gripper_prep(recipe, has_source_script=has_source_script, result=result)
        _lint_capbc_input_sub_location(
            spec,
            recipe,
            capbc_steps,
            result=result,
        )


def _lint_capbc_gripper_prep(
    recipe: Mapping[str, Any],
    *,
    has_source_script: bool,
    result: Any,
) -> None:
    explicit = explicit_gripper_values_from_recipe(recipe)
    close_value = explicit.get(GRIPPER_CLOSE_VARIABLE, 0)
    open_value = explicit.get(GRIPPER_OPEN_VARIABLE, 0)
    close_bad = needs_prep_fixup(GRIPPER_CLOSE_VARIABLE, close_value)
    open_bad = needs_prep_fixup(GRIPPER_OPEN_VARIABLE, open_value)

    if not close_bad and not open_bad:
        return

    if has_source_script and GRIPPER_CLOSE_VARIABLE not in explicit and GRIPPER_OPEN_VARIABLE not in explicit:
        return

    problems = []
    if close_bad:
        problems.append("GripperClose")
    if open_bad:
        problems.append("GripperOpen")
    joined = " and ".join(problems)
    result.add(
        "warning",
        "verification_recipe",
        f"CapBC prep {joined} would remain 0 after recipe-level resolution; Script Editor "
        "Infopad may report target width out of range. Set verification_recipe.capbc_prep, "
        "tube_gripper_close/tube_gripper_open, prep_steps set_variable entries, or "
        "source.source_scripts so generation can mine those values from the imported ZEIA. "
        "The pipeline does not invent grip widths.",
    )


def _lint_capbc_input_sub_location(
    spec: Mapping[str, Any],
    recipe: Mapping[str, Any],
    capbc_steps: list[tuple[str, dict[str, Any], str]],
    *,
    result: Any,
) -> None:
    deck_location, _reason = resolve_tube_deck_location(recipe, manifest=None)
    if deck_location:
        return

    source = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    has_import_context = any(
        str(source.get(key) or "").strip()
        for key in ("context", "collection")
    ) or bool(source.get("contexts"))
    if has_import_context:
        return

    for location, step, subroutine_name in capbc_steps:
        mappings = recipe_step_variable_mappings(step)
        for mi, mapping in enumerate(mappings):
            if not mapping_needs_input_sub_location_fix(mapping):
                continue
            result.add(
                "warning",
                f"{location}.variable_mappings_start[{mi}]",
                f"CapBC subroutine {subroutine_name!r} maps {INPUT_SUB_LOCATION_TARGET} to "
                f"{mapping.get('source')!r}, but no tube deck location can be resolved from "
                "verification_recipe.tube_deck_location, protocol IR variables, or mined "
                "script startup defaults (TubeLocationName / InputSubLocation) validated "
                "against imported worktable geometry. Infopad may show Location '0' not "
                "found. Set verification_recipe.tube_deck_location, or import a ZEIA "
                "context whose scripts and worktable geometry declare that location.",
            )
