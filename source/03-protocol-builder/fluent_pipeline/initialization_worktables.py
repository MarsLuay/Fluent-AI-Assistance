"""Detect initialization worktable candidates and annotate generated scripts.

When a script-bound worktable cannot initialize the instrument, operators often
switch to a maintenance or setup deck, initialize there, then open the script on
its bound worktable. This module detects compatible init decks from the imported
ZEIA catalog, injects an opening ``comment`` step into the protocol IR, and
supplies fallback names for Gate 27 ``InitializeInstrument`` retries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .protocol_ir import canonical_setup_group_name_for_steps
from .worktable_ir import initialization_worktable_from_spec, worktable_guid_from_ir, worktable_name_from_ir

INITIALIZATION_COMMENT_REASON = "instrument_initialization_worktable_notice"
INITIALIZATION_SAFETY_FLAG = "instrument_initialization_worktable_notice"
INITIALIZATION_ASSUMPTION_ID = "instrument_initialization_worktable_fallback"
# Operator-facing comment lists only the top detected init deck; full ordering stays
# in ``InitializationWorktablePlan.fallback_init_worktables`` for Gate 27 retries.
MAX_OPERATOR_FALLBACK_WORKTABLES = 1


def operator_fallback_worktables(fallbacks: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return the short fallback list shown in the opening initialization comment."""
    names = tuple(name for name in fallbacks if str(name or "").strip())
    return names[:MAX_OPERATOR_FALLBACK_WORKTABLES]


# Soft ranking only — never invent init decks from filename tokens like `_wt_v` /
# `empty` / `systemcare`. Primary sources are recipe ``initialization_worktable``,
# exported ZEIA script→worktable bindings, then Script→WorktableWorkspace refs
# (called init / subroutine bindings). Soft location/FCA score is fallback only.
_SCRIPT_BOUND_SCORE = 80
_ZEIA_BINDING_SCORE = 150
_RECIPE_INIT_SCORE = 200
_FCA_WASTE_BONUS = 40
_LOCATION_OVERLAP_CAP = 40
_REQUIRED_LOCATION_CAP = 30



@dataclass(frozen=True)
class InitializationWorktableCandidate:
    name: str
    guid: str | None = None
    score: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)
    has_fca_waste: bool = False


@dataclass(frozen=True)
class InitializationWorktablePlan:
    script_worktable: str | None
    script_worktable_guid: str | None
    primary_init_worktable: str | None
    primary_init_worktable_guid: str | None
    primary_init_script: str | None
    fallback_init_worktables: tuple[str, ...]
    candidates: tuple[InitializationWorktableCandidate, ...]

    def comment_text(self) -> str:
        script = self.script_worktable or "(script worktable)"
        init_script = self.primary_init_script or "the initialization"
        if self.primary_init_worktable and (
            not self.script_worktable
            or self.primary_init_worktable.casefold() != self.script_worktable.casefold()
        ):
            return (
                "Initialization won't work on this worktable so initialize on "
                f"{self.primary_init_worktable} worktable in {init_script} script first."
            )
        elif self.fallback_init_worktables:
            return (
                "Initialization won't work on this worktable so initialize on "
                f"{self.fallback_init_worktables[0]} worktable in {init_script} script first."
            )
        return f"Initialize the instrument before running this method on {script}."


def workspace_catalog(manifest: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(manifest, Mapping):
        return []
    geometry = manifest.get("worktable_geometry")
    if isinstance(geometry, Mapping):
        workspaces = geometry.get("workspaces")
        if isinstance(workspaces, list) and workspaces:
            return [dict(item) for item in workspaces if isinstance(item, dict)]
    rows: list[dict[str, Any]] = []
    for item in manifest.get("workspaces") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("object_name") or item.get("name") or "").strip()
        guid = ""
        guids = item.get("guids")
        if isinstance(guids, list) and guids:
            guid = str(guids[0] or "").strip()
        rows.append({"name": name, "object_name": name, "guid": guid or None})
    return rows


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _workspace_by_name(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for workspace in catalog:
        name = str(workspace.get("name") or workspace.get("object_name") or "").strip()
        if name:
            indexed[_norm(name)] = workspace
    return indexed


def _location_signature(workspace: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("location_names", "pin_sites"):
        items = workspace.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            text = str(item or "").strip()
            if text:
                values.add(_norm(text))
    for placement in workspace.get("placements") or []:
        if not isinstance(placement, Mapping):
            continue
        for key in ("site_name", "pin_name", "deck_location", "location"):
            text = str(placement.get(key) or "").strip()
            if text:
                values.add(_norm(text))
    return values


def _workspace_text_values(workspace: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "name",
        "object_name",
        "base_worktable_name",
        "functional_group",
        "footprint",
    ):
        text = str(workspace.get(key) or "").strip()
        if text:
            values.append(text)
    for key in ("location_names", "pin_sites", "connector_guids"):
        for item in workspace.get(key) or []:
            text = str(item or "").strip()
            if text:
                values.append(text)
    for key in ("placements", "available_sites"):
        for item in workspace.get(key) or []:
            if not isinstance(item, Mapping):
                continue
            for field in (
                "label",
                "name",
                "catalog",
                "component_name",
                "site_name",
                "pin_name",
                "deck_location",
                "location",
                "base_site_name",
                "connector_component_name",
                "connector_site_name",
            ):
                text = str(item.get(field) or "").strip()
                if text:
                    values.append(text)
    return values


def _workspace_has_fca_waste(workspace: Mapping[str, Any]) -> bool:
    """Return true when workspace geometry includes FCA liquid/plastics waste."""
    for text in _workspace_text_values(workspace):
        lowered = text.casefold()
        has_waste = "waste" in lowered
        if has_waste and "fca" in lowered:
            return True
        if has_waste and ("plastic" in lowered or "liquid" in lowered):
            return True
    return False


def _required_locations_from_ir(ir: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(ir, Mapping):
        return set()
    locations: set[str] = set()
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        for key in ("to_location", "from_location", "location", "destination_location", "source_location"):
            for source in (params, step):
                if not isinstance(source, Mapping):
                    continue
                text = str(source.get(key) or "").strip()
                if text:
                    locations.add(_norm(text))
        binding = params.get("worktable_labware")
        if isinstance(binding, Mapping):
            text = str(binding.get("location") or binding.get("site") or "").strip()
            if text:
                locations.add(_norm(text))
    for entry in ir.get("labware") or []:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("location") or "").strip()
        if text:
            locations.add(_norm(text))
    return locations


def _subroutine_worktable_affinity(
    ir: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None,
) -> dict[str, int]:
    if not isinstance(ir, Mapping) or not isinstance(manifest, Mapping):
        return {}
    script_rows = [item for item in manifest.get("scripts") or [] if isinstance(item, dict)]
    affinity: dict[str, int] = {}
    called = _called_subroutine_names(ir)
    for subroutine in called:
        binding = _script_bound_worktable_ref(subroutine, script_rows)
        if not binding:
            continue
        for key in (binding.get("name"), binding.get("guid")):
            text = str(key or "").strip()
            if text:
                affinity[_norm(text)] = affinity.get(_norm(text), 0) + 20
    return affinity


def _called_subroutine_names(ir: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(ir, Mapping):
        return []
    names: list[str] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        subroutine = str(params.get("subroutine") or params.get("SubRoutine") or "").strip()
        if not subroutine:
            continue
        names.append(subroutine)
    return names


def _script_display_name(script: Mapping[str, Any]) -> str:
    name = str(script.get("object_name") or "").strip()
    folder = str(script.get("object_path") or "").strip().strip("\\")
    return f"{folder}\\{name}" if folder else name


def _script_matches_name(script: Mapping[str, Any], target: str) -> bool:
    object_name = str(script.get("object_name") or "").strip()
    if not object_name:
        return False
    needle = _norm(target)
    bare = _norm(target.rsplit("\\", 1)[-1])
    key = _norm(object_name)
    display = _norm(_script_display_name(script))
    return (
        key == needle
        or key == bare
        or display == needle
        or key.endswith("\\" + bare)
        or bare in key
    )


def _script_references_worktable(script: Mapping[str, Any], worktable: str | None) -> bool:
    if not worktable:
        return False
    for ref in script.get("references") or []:
        if not isinstance(ref, Mapping):
            continue
        if str(ref.get("type_id") or "") != "WorktableWorkspace":
            continue
        if _norm(ref.get("object_name")) == _norm(worktable):
            return True
    return False


def _script_for_worktable(
    worktable: str | None,
    manifest: Mapping[str, Any] | None,
    ir: Mapping[str, Any] | None,
) -> str | None:
    if not worktable or not isinstance(manifest, Mapping):
        return None
    script_rows = [item for item in manifest.get("scripts") or [] if isinstance(item, dict)]
    matches = [script for script in script_rows if _script_references_worktable(script, worktable)]
    if not matches:
        return None
    called = _called_subroutine_names(ir)
    for subroutine in called:
        for script in matches:
            if _script_matches_name(script, subroutine):
                return _script_display_name(script)
    return _script_display_name(matches[0])


def _script_bound_worktable(subroutine: str, scripts: list[dict[str, Any]]) -> str | None:
    binding = _script_bound_worktable_ref(subroutine, scripts)
    return binding.get("name") if binding else None


def _script_bound_worktable_ref(
    subroutine: str,
    scripts: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Return WorktableWorkspace name/guid bound to a script ObjectName."""
    for script in scripts:
        if not _script_matches_name(script, subroutine):
            continue
        for ref in script.get("references") or []:
            if not isinstance(ref, Mapping):
                continue
            if str(ref.get("type_id") or "") != "WorktableWorkspace":
                continue
            name = str(ref.get("object_name") or ref.get("name") or "").strip()
            guid = str(ref.get("guid") or "").strip()
            if name or guid:
                return {"name": name, "guid": guid}
        deps = script.get("dependencies") if isinstance(script.get("dependencies"), Mapping) else {}
        for value in deps.get("workspace_guids") or []:
            guid = str(value or "").strip()
            if guid:
                return {"name": "", "guid": guid}
    return None


def _score_init_candidate(
    workspace: Mapping[str, Any],
    *,
    script_worktable: str | None,
    script_record: Mapping[str, Any] | None,
    required_locations: set[str],
    subroutine_affinity: Mapping[str, int],
    zeia_bindings: Mapping[str, dict[str, Any]],
    allow_soft_score: bool,
    spec_init_worktable: str | None,
    spec_init_guid: str | None,
) -> InitializationWorktableCandidate | None:
    name = str(workspace.get("name") or workspace.get("object_name") or "").strip()
    if not name:
        return None
    if script_worktable and _norm(name) == _norm(script_worktable):
        return None
    guid = str(workspace.get("guid") or "").strip() or None
    score = 0
    reasons: list[str] = []

    recipe_name_match = bool(spec_init_worktable and _norm(name) == _norm(spec_init_worktable))
    recipe_guid_match = bool(spec_init_guid and guid and _norm(guid) == _norm(spec_init_guid))
    if recipe_name_match or recipe_guid_match:
        score += _RECIPE_INIT_SCORE
        reasons.append("requested initialization_worktable")

    binding = zeia_bindings.get(_norm(name))
    if binding is None and guid:
        binding = zeia_bindings.get(_norm(guid))
    if binding is not None:
        score += _ZEIA_BINDING_SCORE
        script_label = str(binding.get("script") or binding.get("display_name") or "").strip()
        if script_label:
            reasons.append(f"exported ZEIA script binding ({script_label})")
        else:
            reasons.append("exported ZEIA script→worktable binding")

    affinity = int(subroutine_affinity.get(_norm(name), 0))
    if guid:
        affinity = max(affinity, int(subroutine_affinity.get(_norm(guid), 0)))
    if affinity:
        # Normalize call-count affinity into a stable script-bound signal.
        score += _SCRIPT_BOUND_SCORE + min(60, affinity)
        reasons.append("called subroutine(s) bind to this worktable")

    # Soft location / FCA bonuses only when no ZEIA binding catalog is present,
    # or as tie-breaks for already-bound decks (never sole invent reason when
    # exported bindings exist for other worktables).
    has_fca_waste = _workspace_has_fca_waste(workspace)
    if allow_soft_score or score > 0:
        candidate_locations = _location_signature(workspace)
        if script_record is not None:
            overlap = candidate_locations & _location_signature(script_record)
            if overlap and (allow_soft_score or score > 0):
                score += min(_LOCATION_OVERLAP_CAP, 5 * len(overlap))
                reasons.append(f"{len(overlap)} shared deck location(s) with script worktable")
        if required_locations and candidate_locations and (allow_soft_score or score > 0):
            required_overlap = candidate_locations & required_locations
            if required_overlap:
                score += min(_REQUIRED_LOCATION_CAP, 5 * len(required_overlap))
                reasons.append(f"{len(required_overlap)} IR-required location(s) present")
        if has_fca_waste and score > 0:
            score += _FCA_WASTE_BONUS
            reasons.append("FCA liquid/plastics waste present")

    if score <= 0:
        return None
    return InitializationWorktableCandidate(
        name=name,
        guid=guid,
        score=score,
        reasons=tuple(reasons),
        has_fca_waste=has_fca_waste,
    )


def detect_initialization_worktable_candidates(
    manifest: Mapping[str, Any] | None,
    *,
    ir: Mapping[str, Any] | None = None,
    spec: Mapping[str, Any] | None = None,
) -> tuple[InitializationWorktableCandidate, ...]:
    catalog = workspace_catalog(manifest)
    if not catalog:
        return ()
    script_worktable = worktable_name_from_ir(ir)
    if not script_worktable and isinstance(spec, Mapping):
        recipe = spec.get("verification_recipe")
        if isinstance(recipe, Mapping):
            script_worktable = str(recipe.get("worktable") or "").strip() or None
    spec_init, spec_init_guid = initialization_worktable_from_spec(spec)
    by_name = _workspace_by_name(catalog)
    script_record = by_name.get(_norm(script_worktable or ""))
    required_locations = _required_locations_from_ir(ir)
    subroutine_affinity = _subroutine_worktable_affinity(ir, manifest)
    zeia_bindings = _zeia_binding_index(manifest, ir=ir)
    # When ZEIA exported script→worktable bindings exist, soft score is fallback
    # only (still applied as tie-break on already-bound decks).
    allow_soft_score = not bool(zeia_bindings)
    scored: list[InitializationWorktableCandidate] = []
    for workspace in catalog:
        candidate = _score_init_candidate(
            workspace,
            script_worktable=script_worktable,
            script_record=script_record,
            required_locations=required_locations,
            subroutine_affinity=subroutine_affinity,
            zeia_bindings=zeia_bindings,
            allow_soft_score=allow_soft_score,
            spec_init_worktable=spec_init,
            spec_init_guid=spec_init_guid,
        )
        if candidate is not None:
            scored.append(candidate)
    scored.sort(
        key=lambda item: (
            not any(reason.startswith("requested initialization_worktable") for reason in item.reasons),
            not any("exported ZEIA" in reason for reason in item.reasons),
            not any("bind to this worktable" in reason for reason in item.reasons),
            not item.has_fca_waste,
            -item.score,
            item.name.casefold(),
        )
    )
    return tuple(scored)


def _zeia_binding_index(
    manifest: Mapping[str, Any] | None,
    *,
    ir: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Index exported ZEIA script→worktable bindings by worktable name/guid.

    Prefer bindings for IR-called subroutines when present; otherwise keep the
    full exported binding set so init can prefer ZEIA truth over soft score.
    """
    from .script_folder_bindings_export import zeia_worktable_bindings_from_manifest

    rows = zeia_worktable_bindings_from_manifest(manifest)
    if not rows:
        return {}
    called = {_norm(name) for name in _called_subroutine_names(ir)}
    preferred: list[dict[str, Any]] = []
    if called:
        for row in rows:
            script = str(row.get("script") or row.get("display_name") or "").strip()
            display = str(row.get("display_name") or "").strip()
            bare = script.rsplit("\\", 1)[-1] if script else ""
            if (
                _norm(script) in called
                or _norm(display) in called
                or _norm(bare) in called
                or any(_norm(script).endswith("\\" + needle) or needle in _norm(script) for needle in called if needle)
            ):
                preferred.append(row)
    selected = preferred or list(rows)
    indexed: dict[str, dict[str, Any]] = {}
    for row in selected:
        for key in (row.get("worktable_name"), row.get("worktable_guid")):
            text = str(key or "").strip()
            if text:
                indexed[_norm(text)] = row
    return indexed


def build_initialization_worktable_plan(
    manifest: Mapping[str, Any] | None,
    *,
    ir: Mapping[str, Any] | None = None,
    spec: Mapping[str, Any] | None = None,
) -> InitializationWorktablePlan | None:
    script_worktable = worktable_name_from_ir(ir)
    script_guid = worktable_guid_from_ir(ir)
    if not script_worktable and isinstance(spec, Mapping):
        recipe = spec.get("verification_recipe")
        if isinstance(recipe, Mapping):
            script_worktable = str(recipe.get("worktable") or "").strip() or None
            script_guid = str(recipe.get("worktable_guid") or "").strip() or script_guid
    spec_init, spec_init_guid = initialization_worktable_from_spec(spec)
    candidates = detect_initialization_worktable_candidates(manifest, ir=ir, spec=spec)
    if not script_worktable and not spec_init and not candidates:
        return None

    primary = spec_init
    primary_guid = spec_init_guid
    if primary and not primary_guid:
        match = next((item for item in candidates if _norm(item.name) == _norm(primary)), None)
        if match is not None:
            primary_guid = match.guid
    if not primary and candidates:
        # Prefer recipe-matched / script-bound candidates already sorted above.
        preferred_candidate = candidates[0]
        primary = preferred_candidate.name
        primary_guid = preferred_candidate.guid
    elif primary and candidates:
        # Keep recipe pin unless it is absent from the catalog and a script-bound
        # FCA deck is available as a safer mined alternative.
        spec_candidate = next((item for item in candidates if _norm(item.name) == _norm(spec_init)), None)
        script_bound = [
            item
            for item in candidates
            if any("bind to this worktable" in reason for reason in item.reasons)
        ]
        if spec_candidate is None and script_bound:
            preferred_candidate = script_bound[0]
            primary = preferred_candidate.name
            primary_guid = preferred_candidate.guid

    fallback_names: list[str] = []
    seen = {_norm(script_worktable), _norm(primary)}
    for candidate in candidates:
        key = _norm(candidate.name)
        if key in seen:
            continue
        seen.add(key)
        fallback_names.append(candidate.name)
    if primary and script_worktable and _norm(primary) == _norm(script_worktable):
        primary = fallback_names[0] if fallback_names else None
        primary_guid = next((item.guid for item in candidates if item.name == primary), None)
        if primary:
            fallback_names = [name for name in fallback_names if _norm(name) != _norm(primary)]
    if not primary and not fallback_names and not script_worktable:
        return None
    primary_init_script = _script_for_worktable(primary, manifest, ir)
    return InitializationWorktablePlan(
        script_worktable=script_worktable,
        script_worktable_guid=script_guid,
        primary_init_worktable=primary,
        primary_init_worktable_guid=primary_guid,
        primary_init_script=primary_init_script,
        fallback_init_worktables=tuple(fallback_names),
        candidates=candidates,
    )


def fallback_names_for_initialize_step(
    plan: InitializationWorktablePlan | None,
    *,
    phase: str,
    step_name: str,
) -> tuple[str, ...]:
    if plan is None:
        return ()
    if phase == "pre_initialize":
        return plan.fallback_init_worktables
    if phase != "script_workspace":
        return ()
    if plan.primary_init_worktable and _norm(step_name) == _norm(plan.script_worktable):
        # Script deck failed to init: retry detected setup decks before aborting.
        names: list[str] = []
        if plan.primary_init_worktable:
            names.append(plan.primary_init_worktable)
        names.extend(plan.fallback_init_worktables)
        deduped: list[str] = []
        seen = {_norm(step_name)}
        for name in names:
            key = _norm(name)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(name)
        return tuple(deduped)
    return ()


def annotate_initialization_worktable_comment(
    ir: dict[str, Any],
    source_manifest: dict[str, Any] | None,
    spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert an operator-facing initialization comment as the first IR step."""
    if not isinstance(ir, dict):
        return ir
    plan = build_initialization_worktable_plan(source_manifest, ir=ir, spec=spec)
    if plan is None:
        return ir
    steps = ir.get("steps")
    if not isinstance(steps, list):
        steps = []
        ir["steps"] = steps
    first_group = canonical_setup_group_name_for_steps([step for step in steps if isinstance(step, dict)])
    if steps and _is_initialization_notice_step(steps[0]):
        comment_step = steps[0]
        if not isinstance(comment_step, dict):
            comment_step = {}
            steps[0] = comment_step
        params = comment_step.setdefault("parameters", {})
        if not isinstance(params, dict):
            params = {}
            comment_step["parameters"] = params
        comment_step["command_id"] = "CommentStatement"
        comment_step["group"] = comment_step.get("group") or first_group
        comment_step["id"] = comment_step.get("id") or "step_init_worktable_notice"
        comment_step["name"] = "Instrument initialization"
        comment_step["operation"] = "comment"
        comment_step["safety_flags"] = [INITIALIZATION_SAFETY_FLAG]
    else:
        comment_step = {
            "command_id": "CommentStatement",
            "group": first_group,
            "id": "step_init_worktable_notice",
            "index": 1,
            "name": "Instrument initialization",
            "operation": "comment",
            "parameters": {},
            "safety_flags": [INITIALIZATION_SAFETY_FLAG],
        }
        ir["steps"] = [comment_step, *steps]
        _renumber_ir_steps(ir)
    params = comment_step.setdefault("parameters", {})
    if not isinstance(params, dict):
        params = {}
        comment_step["parameters"] = params
    params.update(
        {
            "comment": plan.comment_text(),
            "reason": INITIALIZATION_COMMENT_REASON,
            "script_worktable": plan.script_worktable,
            "primary_init_worktable": plan.primary_init_worktable,
            "primary_init_script": plan.primary_init_script,
            "fallback_init_worktables": list(operator_fallback_worktables(plan.fallback_init_worktables)),
            "fallback_init_worktables_total": len(plan.fallback_init_worktables),
        }
    )
    source = ir.setdefault("source", {})
    source["initialization_worktable_plan"] = {
        "script_worktable": plan.script_worktable,
        "script_worktable_guid": plan.script_worktable_guid,
        "primary_init_worktable": plan.primary_init_worktable,
        "primary_init_worktable_guid": plan.primary_init_worktable_guid,
        "primary_init_script": plan.primary_init_script,
        "fallback_init_worktables": list(plan.fallback_init_worktables),
        "candidates": [
            {
                "name": item.name,
                "guid": item.guid,
                "score": item.score,
                "reasons": list(item.reasons),
                "has_fca_waste": item.has_fca_waste,
            }
            for item in plan.candidates
        ],
    }
    assumptions = ir.setdefault("safety_assumptions", [])
    assumption_text = (
        "Instrument initialization may require switching to a compatible setup worktable "
        "before opening this script-bound worktable. The opening comment step lists only "
        "the preferred compatible initialization worktable; full candidates remain in IR metadata."
    )
    existing_assumption = next(
        (
            item
            for item in assumptions
            if isinstance(item, dict) and item.get("id") == INITIALIZATION_ASSUMPTION_ID
        ),
        None,
    )
    if isinstance(existing_assumption, dict):
        existing_assumption["text"] = assumption_text
    else:
        assumptions.append(
            {
                "id": INITIALIZATION_ASSUMPTION_ID,
                "text": assumption_text,
            }
        )
    return ir


def _is_initialization_notice_step(step: dict[str, Any]) -> bool:
    if str(step.get("operation") or "") != "comment":
        return False
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    if params.get("reason") == INITIALIZATION_COMMENT_REASON:
        return True
    flags = step.get("safety_flags") or []
    return INITIALIZATION_SAFETY_FLAG in flags


def _renumber_ir_steps(ir: dict[str, Any]) -> None:
    for index, step in enumerate(ir.get("steps") or [], start=1):
        if isinstance(step, dict):
            step["index"] = index
