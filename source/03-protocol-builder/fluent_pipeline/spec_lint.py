"""Static linter for ``request.spec.yaml`` (including its verification recipe).

The linter runs BEFORE generation so a malformed spec/recipe fails fast with a
clear, actionable message instead of silently producing an empty protocol IR.
It normalizes a forgiving subset of the spec, then returns structured findings
with severity ``error`` vs ``warning`` and a path-style location
(``verification_recipe.groups[0].steps[2]``).

The recipe checks reuse the shared classifier helpers in :mod:`request_spec`
(``recipe_step_type``, ``recipe_subroutine_name``, ``recipe_step_produces_ir``)
so the linter cannot drift from the recipe-to-IR builder in
:mod:`generation_workflow`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .labware_contracts import (
    recipe_label_catalog_issues,
    resolve_preferred_label_catalogs,
)
from .protocol_ir import (
    CATALOG_DEFAULT_CLASS_NAMES,
    prompt_has_media_boilerplate,
    prompt_looks_like_external_initialization_check,
)
from .request_spec import (
    REQUEST_SPEC_VERSION,
    RECIPE_STEP_SHORTHAND_KEYS,
    RECIPE_STEP_TYPES,
    _load_simple_yaml,
    _yaml_module,
    is_meta_verification_group_comment,
    recipe_group_description,
    recipe_step_type,
    recipe_subroutine_name,
    request_verbatim_prompt,
)

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class LintFinding:
    """A single linter finding with severity, message, and location path."""

    severity: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.location}: {self.message}"


@dataclass
class LintResult:
    """Structured result of linting a request spec."""

    findings: list[LintFinding] = field(default_factory=list)
    estimated_ir_body_steps: int = 0

    @property
    def errors(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def ok(self) -> bool:
        """True when there are no error-level findings."""
        return not self.errors

    def add(self, severity: str, location: str, message: str) -> None:
        self.findings.append(LintFinding(severity, location, message))


def lint_request_spec_file(path: Path) -> LintResult:
    """Load a request spec from disk (without normalizing) and lint it.

    Linting works on the RAW spec so user-supplied type mistakes (which
    normalization would silently coerce) are still reported. Load/parse failures
    are reported as error findings rather than raised, so callers always get a
    single uniform result object.
    """
    result = LintResult()
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.add(ERROR, "<file>", f"request spec not found: {path}")
        return result
    except OSError as exc:
        result.add(ERROR, "<file>", f"could not read request spec: {exc}")
        return result

    try:
        payload = _parse_spec_text(text, path.suffix.lower())
    except ValueError as exc:
        result.add(ERROR, "<file>", f"could not parse request spec: {exc}")
        return result

    return lint_request_spec(payload)


def _parse_spec_text(text: str, suffix: str) -> Any:
    import json

    if suffix in {".yaml", ".yml"}:
        yaml = _yaml_module(required=False)
        if yaml is not None:
            return yaml.safe_load(text)
        return _load_simple_yaml(text)
    return json.loads(text)


def lint_request_spec(
    spec: Any,
    *,
    preferred_label_catalogs: Mapping[str, str] | None = None,
    context: Any = None,
) -> LintResult:
    """Lint an in-memory (raw) request spec mapping and return findings."""
    result = LintResult()
    if not isinstance(spec, dict):
        result.add(
            ERROR,
            "<root>",
            f"request spec must be a mapping; got {type(spec).__name__}.",
        )
        return result
    preferred = preferred_label_catalogs
    if preferred is None:
        preferred = resolve_preferred_label_catalogs(spec=spec, context=context)
    _lint_mapping(spec, result, preferred_label_catalogs=preferred)
    return result


def _lint_mapping(
    spec: dict[str, Any],
    result: LintResult,
    *,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> None:
    version = spec.get("schema_version")
    if version is not None and version != REQUEST_SPEC_VERSION:
        result.add(
            ERROR,
            "schema_version",
            f"unsupported schema_version {version!r}; expected {REQUEST_SPEC_VERSION!r}.",
        )

    verbatim = request_verbatim_prompt(spec).strip()
    if not verbatim:
        result.add(
            ERROR,
            "request.intent",
            "no request intent/verbatim prompt found; there is nothing to generate from. "
            "Set request.intent (or a top-level intent).",
        )

    _lint_source(spec, result)
    _lint_generation(spec, result)
    _lint_acceptance(spec, result)
    _lint_verification_recipe(
        spec,
        result,
        preferred_label_catalogs=preferred_label_catalogs,
    )


def _lint_source(spec: dict[str, Any], result: LintResult) -> None:
    source = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    archives = source.get("project_archives") or []
    context = source.get("context")
    contexts = source.get("contexts") or []
    collection = source.get("collection")
    if not archives and not context and not contexts and not collection:
        result.add(
            WARNING,
            "source",
            "no source.project_archives, context, contexts, or collection set; generation may "
            "not find a base ZEIA (will rely on the active project context if one is set).",
        )


def _lint_generation(spec: dict[str, Any], result: LintResult) -> None:
    generation = spec.get("generation") if isinstance(spec.get("generation"), dict) else {}
    if "preserve_regeneration_baseline" in generation:
        value = generation.get("preserve_regeneration_baseline")
        if not isinstance(value, bool):
            result.add(
                ERROR,
                "generation.preserve_regeneration_baseline",
                f"must be a boolean; got {type(value).__name__}.",
            )
    if "prompt_only" in generation:
        value = generation.get("prompt_only")
        if value is not None and not isinstance(value, bool):
            result.add(
                WARNING,
                "generation.prompt_only",
                f"should be a boolean or null; got {type(value).__name__}.",
            )
    _lint_catalog_defaults(generation, result)


def _lint_catalog_defaults(generation: dict[str, Any], result: LintResult) -> None:
    if "catalog_defaults" not in generation:
        return
    value = generation.get("catalog_defaults")
    if value is None:
        return
    if not isinstance(value, dict):
        result.add(
            ERROR,
            "generation.catalog_defaults",
            f"must be a mapping of labware class name to catalog string; got {type(value).__name__}.",
        )
        return
    for class_name, catalog_name in value.items():
        location = f"generation.catalog_defaults.{class_name!r}"
        if not isinstance(class_name, str) or not class_name.strip():
            result.add(
                ERROR,
                location,
                "class key must be a non-empty string labware class name.",
            )
            continue
        if class_name not in CATALOG_DEFAULT_CLASS_NAMES:
            known = ", ".join(sorted(CATALOG_DEFAULT_CLASS_NAMES))
            result.add(
                ERROR,
                location,
                f"unknown labware class {class_name!r}; expected one of: {known}.",
            )
        if not isinstance(catalog_name, str) or not str(catalog_name).strip():
            result.add(
                ERROR,
                location,
                "catalog name must be a non-empty string.",
            )


def _lint_acceptance(spec: dict[str, Any], result: LintResult) -> None:
    acceptance = spec.get("acceptance") if isinstance(spec.get("acceptance"), dict) else {}
    if "required_checks" in acceptance and not isinstance(acceptance.get("required_checks"), list):
        result.add(
            WARNING,
            "acceptance.required_checks",
            f"should be a list of check strings; got {type(acceptance.get('required_checks')).__name__}.",
        )
    if "enforce_prompt_coverage" in acceptance and not isinstance(acceptance.get("enforce_prompt_coverage"), bool):
        result.add(
            WARNING,
            "acceptance.enforce_prompt_coverage",
            f"should be a boolean; got {type(acceptance.get('enforce_prompt_coverage')).__name__}.",
        )


def _lint_verification_recipe(
    spec: dict[str, Any],
    result: LintResult,
    *,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> None:
    recipe = spec.get("verification_recipe")
    if recipe is None:
        # No declarative recipe: the seed-IR path will run instead. Nothing to lint.
        return
    if not isinstance(recipe, dict):
        result.add(
            ERROR,
            "verification_recipe",
            f"verification_recipe must be a mapping; got {type(recipe).__name__}.",
        )
        return

    _lint_recipe_labware(
        recipe,
        result,
        preferred_label_catalogs=preferred_label_catalogs,
    )
    _lint_recipe_prep_steps(recipe, result)

    worktable = str(recipe.get("worktable") or "").strip()
    init_worktable = str(recipe.get("initialization_worktable") or "").strip()
    if init_worktable and worktable and init_worktable.casefold() == worktable.casefold():
        result.add(
            WARNING,
            "verification_recipe.initialization_worktable",
            "initialization_worktable matches worktable; omit initialization_worktable "
            "unless hardware init must happen on a different deck before the script workspace.",
        )
    if init_worktable and not worktable:
        result.add(
            WARNING,
            "verification_recipe.initialization_worktable",
            "initialization_worktable is set but worktable is empty; set worktable to the "
            "script-bound workspace Gate 27 should switch to after initialize.",
        )

    groups = recipe.get("groups")
    if not isinstance(groups, list) or not groups:
        result.add(
            ERROR,
            "verification_recipe.groups",
            "verification_recipe is present but declares no groups; this produces an empty "
            "protocol IR. Add at least one group with steps, or remove the recipe.",
        )
        return

    body_step_count = 0
    empty_groups: list[str] = []
    for gi, group in enumerate(groups):
        loc = f"verification_recipe.groups[{gi}]"
        if not isinstance(group, dict):
            result.add(ERROR, loc, f"group must be a mapping; got {type(group).__name__}.")
            continue
        group_label = str(group.get("name") or f"#{gi}")
        group_description = recipe_group_description(group)
        if not group_description:
            result.add(
                WARNING,
                loc,
                f"group {group_label!r} has no description; add verification_recipe.groups[{gi}].description "
                "with a short purpose comment (for example: 'Tests and confirms arm positioning').",
            )
        steps = group.get("steps")
        if not isinstance(steps, list) or not steps:
            empty_groups.append(group_label)
            continue
        group_body_steps = 0
        for si, step in enumerate(steps):
            step_loc = f"{loc}.steps[{si}]"
            if _lint_recipe_step(step, step_loc, result):
                group_body_steps += 1
        if group_body_steps == 0:
            empty_groups.append(group_label)
        body_step_count += group_body_steps

    result.estimated_ir_body_steps = body_step_count

    if body_step_count == 0:
        empty_desc = ", ".join(empty_groups) if empty_groups else "all groups"
        result.add(
            ERROR,
            "verification_recipe.groups",
            "the verification recipe would produce 0 IR body steps (empty-IR trap). "
            f"No group contributes a usable step (empty/unusable: {empty_desc}). "
            "Add comment/prompt/subroutine/move steps so generation has something to emit.",
        )
    else:
        for label in empty_groups:
            result.add(
                WARNING,
                "verification_recipe.groups",
                f"group {label!r} has no usable steps and will contribute nothing to the IR.",
            )

    if body_step_count > 0:
        try:
            from .recipe_capbc_lint import lint_capbc_scantubes_recipe
        except ImportError:
            lint_capbc_scantubes_recipe = None
        if lint_capbc_scantubes_recipe is not None:
            lint_capbc_scantubes_recipe(spec, recipe, result)


def _lint_recipe_step(step: Any, location: str, result: LintResult) -> bool:
    """Lint one recipe step. Returns True if it would emit a body IR step."""
    if not isinstance(step, dict):
        result.add(
            ERROR,
            location,
            f"step must be a mapping; got {type(step).__name__}.",
        )
        return False

    step_type = recipe_step_type(step)
    if step_type is None:
        keys = ", ".join(RECIPE_STEP_SHORTHAND_KEYS)
        result.add(
            ERROR,
            location,
            f"step has neither a recognized shorthand key ({keys}) nor a valid 'type'.",
        )
        return False
    if step_type not in RECIPE_STEP_TYPES:
        result.add(
            ERROR,
            location,
            f"unrecognized step type {step_type!r}; expected one of "
            f"{', '.join(RECIPE_STEP_TYPES)} (or a 'manual_move'/'verified_move' shorthand).",
        )
        return False

    if step_type == "comment":
        text = str(step.get("comment") or step.get("text") or "").strip()
        if not text:
            result.add(ERROR, location, "comment step has empty text.")
        elif is_meta_verification_group_comment(text):
            result.add(
                WARNING,
                location,
                "comment restates script/worktable metadata and will be dropped; move this text to "
                f"{location.rsplit('.steps', 1)[0]}.description instead.",
            )
        return True
    if step_type == "prompt":
        text = str(step.get("prompt") or step.get("text") or "").strip()
        if not text:
            result.add(ERROR, location, "prompt step has empty text.")
        elif prompt_has_media_boilerplate(text):
            result.add(
                WARNING,
                location,
                "prompt text includes media-attachment boilerplate; write a short operator "
                'instruction instead (for example "Make sure the arms are parallel and do not '
                'wiggle"). Media slots are added automatically during generation.',
            )
        if step.get("worktable_binding") and not step.get("deck_presence_check"):
            result.add(
                WARNING,
                location,
                "worktable_binding is ignored unless deck_presence_check: true; use "
                "deck_presence_check only for initial on-deck placement checks. "
                "Post-move and teaching prompts compile to RUP Standard without deck binding.",
            )
        if step.get("deck_presence_check") and step.get("instrument_init_check"):
            result.add(
                ERROR,
                location,
                "deck_presence_check and instrument_init_check are mutually exclusive; use "
                "instrument_init_check for external machine initialization (RUP Standard) "
                "and deck_presence_check only for on-deck labware placement (RUP Worktable).",
            )
        if step.get("instrument_init_check") and step.get("plain_prompt"):
            result.add(
                WARNING,
                location,
                "instrument_init_check should use RUP Standard media prompts; remove "
                "plain_prompt: true so image/GIF slots are emitted for the init walkthrough.",
            )
        if prompt_looks_like_external_initialization_check(text) and not step.get("instrument_init_check"):
            result.add(
                WARNING,
                location,
                "external-device initialization prompts should set instrument_init_check: true "
                "so they compile to RUP Standard with media, not plain or Worktable prompts.",
            )
        if step.get("deck_presence_check") and not step.get("worktable_binding"):
            result.add(
                ERROR,
                location,
                "deck_presence_check requires worktable_binding (or worktable_patterns entry) "
                "so RUP Worktable can highlight the labware on the deck.",
            )
        return True
    if step_type == "subroutine":
        name = recipe_subroutine_name(step)
        if not name:
            result.add(ERROR, location, "subroutine step has no subroutine name.")
            return False
        return True
    if step_type == "set_variable":
        payload = step.get("set_variable") if isinstance(step.get("set_variable"), dict) else step
        variable = str(payload.get("variable") or "").strip()
        if not variable:
            result.add(ERROR, location, "set_variable step has no variable name.")
            return False
        if payload.get("value") is None or str(payload.get("value")).strip() == "":
            result.add(ERROR, location, f"set_variable step for {variable!r} has no value.")
            return False
        return True
    # move / manual_move / verified_move: always emits a step; labware reference is optional.
    return True


def _lint_recipe_prep_steps(recipe: dict[str, Any], result: LintResult) -> None:
    prep_steps = recipe.get("prep_steps")
    if prep_steps is None:
        return
    if not isinstance(prep_steps, list):
        result.add(
            WARNING,
            "verification_recipe.prep_steps",
            f"prep_steps should be a list; got {type(prep_steps).__name__} (ignored by the IR builder).",
        )
        return
    for pi, step in enumerate(prep_steps):
        _lint_recipe_step(step, f"verification_recipe.prep_steps[{pi}]", result)


def _lint_recipe_labware(
    recipe: dict[str, Any],
    result: LintResult,
    *,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> None:
    labware = recipe.get("labware")
    if labware is None:
        return
    if not isinstance(labware, list):
        result.add(
            WARNING,
            "verification_recipe.labware",
            f"labware should be a list; got {type(labware).__name__} (ignored by the IR builder).",
        )
        return
    for li, entry in enumerate(labware):
        loc = f"verification_recipe.labware[{li}]"
        if not isinstance(entry, dict):
            result.add(
                ERROR,
                loc,
                f"labware entry must be a mapping with a 'label'; got {type(entry).__name__}.",
            )
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            result.add(ERROR, loc, "labware entry is missing 'label' (it will be skipped).")
            continue
        catalog = str(entry.get("catalog") or entry.get("labware_type") or "").strip()
        if not catalog:
            result.add(
                WARNING,
                loc,
                f"labware {label!r} has no 'catalog'/'labware_type'; the IR will use an empty type.",
            )
        location = str(entry.get("location") or "").strip()
        has_site = entry.get("site") is not None or entry.get("position") is not None
        if not location and not has_site:
            result.add(
                WARNING,
                loc,
                f"labware {label!r} has no 'location' or 'site'/'position'; the IR will rely on defaults.",
            )
    for issue in recipe_label_catalog_issues(recipe, preferred_label_catalogs):
        result.add(ERROR, issue["path"], issue["message"])


def render_lint_report(result: LintResult, *, source: str | None = None) -> str:
    """Render a human-readable report grouped by severity with a summary."""
    lines: list[str] = []
    if source:
        lines.append(f"Linting request spec: {source}")
    errors = result.errors
    warnings = result.warnings
    if errors:
        lines.append("")
        lines.append(f"Errors ({len(errors)}):")
        for finding in errors:
            lines.append(f"  - {finding.location}: {finding.message}")
    if warnings:
        lines.append("")
        lines.append(f"Warnings ({len(warnings)}):")
        for finding in warnings:
            lines.append(f"  - {finding.location}: {finding.message}")
    lines.append("")
    if result.estimated_ir_body_steps:
        lines.append(f"Estimated IR body steps from recipe: {result.estimated_ir_body_steps}")
    if errors:
        lines.append(f"Result: FAILED ({len(errors)} error(s), {len(warnings)} warning(s)).")
    elif warnings:
        lines.append(f"Result: OK with {len(warnings)} warning(s).")
    else:
        lines.append("Result: OK (no errors or warnings).")
    return "\n".join(lines).lstrip("\n")
