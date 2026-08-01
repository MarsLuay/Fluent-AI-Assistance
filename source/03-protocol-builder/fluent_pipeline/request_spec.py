"""Request spec helpers for the Codex-facing generation workflow."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .generation_options import (
    GenerationOptions,
    generation_options_from_request_spec,
    normalize_generation_options,
)
from .protocol_ir import protocol_is_prompt_only
from .query_variable_audit import (
    audit_query_variables_for_workflow,
    render_query_variable_audit_markdown,
    validation_diff_check_for_query_audit,
)
from .runtime_variable_audit import (
    audit_runtime_variables_for_workflow,
    render_runtime_variable_audit_markdown,
    validation_diff_check_for_runtime_audit,
)


REQUEST_SPEC_VERSION = "tecan.request_spec.v1"
REQUEST_VALIDATION_DIFF_VERSION = "tecan.request_validation_diff.v1"
RECIPE_STEP_SHORTHAND_KEYS = (
    "comment",
    "prompt",
    "query",
    "query_variable",
    "runtime_variable_prompt",
    "execute_vb_script",
    "subroutine",
    "set_variable",
    "verified_move",
    "move",
    "manual_move",
    "liha_dispense",
    "a200_dispense",
)
RECIPE_STEP_TYPES = (
    "comment",
    "prompt",
    "query_variable",
    "runtime_variable_prompt",
    "execute_vb_script",
    "subroutine",
    "set_variable",
    "move",
    "liha_dispense",
    "a200_dispense",
)
_META_GROUP_COMMENT_PATTERNS = (
    re.compile(r"^\s*verification\s+script\s+for\b", re.I),
    re.compile(r"\busing\s+script\s+\d", re.I),
    re.compile(r"\bworktable\s+\S+", re.I),
    re.compile(r"^\s*generated\s+artifacts\b", re.I),
    re.compile(r"^\s*this\s+ir\s+entered\b", re.I),
    # Variable / setup bookkeeping belongs in ready-to-import notes, not XSCR.
    re.compile(r"^\s*always-run\s+setup\b", re.I),
    re.compile(r"\bfinal\s+sign-off\s+based\s+on\s+setup\s+variables\b", re.I),
    re.compile(r"^\s*variable\s+group\b", re.I),
    re.compile(r"^\s*subgroup:\s*", re.I),
    re.compile(r"\bsetup\s+variables\s*:", re.I),
)


def is_meta_verification_group_comment(text: str) -> bool:
    normalized = str(text or "").strip()
    return bool(normalized and any(pattern.search(normalized) for pattern in _META_GROUP_COMMENT_PATTERNS))


def recipe_group_description(group: Mapping[str, Any] | None) -> str:
    if not isinstance(group, dict):
        return ""
    return str(group.get("description") or "").strip()


def recipe_step_type(raw_step: Any) -> str | None:
    if not isinstance(raw_step, dict):
        return None
    if "type" in raw_step:
        resolved = str(raw_step.get("type") or "").strip().casefold()
        return resolved or None
    key = next((candidate for candidate in RECIPE_STEP_SHORTHAND_KEYS if candidate in raw_step), None)
    if key is None:
        return None
    if key == "query":
        return "query_variable"
    return "move" if key in {"manual_move", "verified_move"} else key


def recipe_step_produces_ir(raw_step: Any) -> bool:
    step_type = recipe_step_type(raw_step)
    if step_type not in RECIPE_STEP_TYPES:
        return False
    return step_type != "subroutine" or bool(recipe_subroutine_name(raw_step))


def recipe_subroutine_name(raw_step: Any) -> str:
    if not isinstance(raw_step, dict):
        return ""
    subroutine = raw_step.get("subroutine") if "subroutine" in raw_step else raw_step.get("name")
    if isinstance(subroutine, dict):
        return str(subroutine.get("name") or "").strip()
    return str(subroutine or "").strip()


def build_request_spec(
    *,
    intent: str,
    protocol_name: str | None = None,
    status: str = "draft",
    context: str | None = None,
    context_kind: str | None = None,
    contexts: list[dict[str, Any]] | None = None,
    project_archives: list[str | Path] | None = None,
    collection: str | None = None,
    source_scripts: list[str] | None = None,
    pattern_refs: list[str] | None = None,
    index_db: str | Path | None = None,
    pattern_ids: list[int | str] | None = None,
    pattern_queries: list[str] | None = None,
    source_script_rank: int = 1,
    generation_options: GenerationOptions | Mapping[str, Any] | None = None,
    simulate: bool | None = None,
    compile_xscr: bool | None = None,
    max_repair_iterations: int | None = None,
    strict_readiness: bool | None = None,
    apply_modeling: bool | None = None,
    verification_prompt_rup: str | None = None,
    target_fluentcontrol_version: str | None = None,
    approve_partial_zeia: bool | None = None,
    approve_deck_layout: bool | None = None,
    approve_command_inventory: bool | None = None,
    approve_unsupported_raw_xml: bool | None = None,
    approved_unsupported_command_ids: list[str] | tuple[str, ...] | None = None,
    waive_checksum_recompute: bool | None = None,
    allow_script_replacement: bool = False,
    fluent_context_check: bool | None = None,
    fluent_provider: str | None = None,
    fluent_timeout: float | None = None,
    fluent_method: str | None = None,
) -> dict[str, Any]:
    """Create a durable user-request contract for a generation run."""
    options = normalize_generation_options(
        generation_options,
        simulate=simulate,
        compile_xscr=compile_xscr,
        max_repair_iterations=max_repair_iterations,
        strict_readiness=strict_readiness,
        apply_modeling=apply_modeling,
        verification_prompt_rup=verification_prompt_rup,
        target_fluentcontrol_version=target_fluentcontrol_version,
        approve_partial_zeia=approve_partial_zeia,
        approve_deck_layout=approve_deck_layout,
        approve_command_inventory=approve_command_inventory,
        approve_unsupported_raw_xml=approve_unsupported_raw_xml,
        approved_unsupported_command_ids=approved_unsupported_command_ids,
        waive_checksum_recompute=waive_checksum_recompute,
        fluent_context_check=fluent_context_check,
        fluent_provider=fluent_provider,
        fluent_timeout=fluent_timeout,
    )
    return normalize_request_spec(
        {
            "schema_version": REQUEST_SPEC_VERSION,
            "request": {
                "intent": intent,
                "verbatim_prompt": intent,
                "original_user_prompt": intent,
                "protocol_name": protocol_name,
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            "source": {
                "context": context,
                "context_kind": context_kind,
                "contexts": contexts or [],
                "project_archives": [str(path) for path in (project_archives or [])],
                "collection": collection,
                "source_scripts": list(source_scripts or []),
                "pattern_refs": list(pattern_refs or []),
                "subroutine_dependencies": [],
                "full_zeia_export": {
                    "required": True,
                    "status": "not_checked",
                    "approved_partial_zeia": options.approve_partial_zeia,
                    "ask_user": (
                        "Provide a full FluentControl ZEIA export, or explicitly approve "
                        "continuing with the detected partial/non-full export."
                    ),
                },
                "pattern_index": {
                    "database": str(index_db) if index_db else None,
                    "pattern_ids": [str(value) for value in (pattern_ids or [])],
                    "pattern_queries": list(pattern_queries or []),
                    "source_script_rank": source_script_rank,
                },
                "host_instrument_configuration": {
                    "status": "unknown",
                    "exact_names": [],
                    "patterns": [],
                    "required": False,
                    "ask_user": (
                        "Before import/run, verify the active FluentControl/VisionX "
                        "instrument configuration matches the target instrument. If it "
                        "does not, switch via the configuration dropdown."
                    ),
                },
            },
            "generation": {
                **options.as_dict(),
                "allow_script_replacement": allow_script_replacement,
                "fluent_method": fluent_method,
            },
            "review": {
                "state": "needs_review",
                "required_before_final_generation": True,
                "decisions": [],
                "unresolved_questions": [],
                "deck_layout": False,
            },
            "acceptance": {
                "validation_diff_required": True,
                "ready_to_import_bundle_required": True,
                "ready_gates": "all_configured",
            },
        }
    )


def load_request_spec(path: Path) -> dict[str, Any]:
    """Load and normalize a request spec from YAML or JSON."""
    if path.suffix.lower() in {".yaml", ".yml"}:
        yaml = _yaml_module(required=False)
        text = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(text) if yaml is not None else _load_simple_yaml(text)
        payload = payload or {}
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request spec must be a mapping")
    return normalize_request_spec(payload)


def write_request_spec(spec: dict[str, Any], path: Path) -> None:
    """Write a request spec as YAML or JSON."""
    payload = normalize_request_spec(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        yaml = _yaml_module(required=False)
        if yaml is not None:
            with path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle, sort_keys=False)
        else:
            path.write_text(_dump_simple_yaml(payload), encoding="utf-8")
        return
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def normalize_request_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults and accept a minimal shorthand shape."""
    if "request" not in spec and "intent" in spec:
        spec = {"request": {"intent": spec.get("intent")}, **{k: v for k, v in spec.items() if k != "intent"}}
    payload = dict(spec)
    payload.setdefault("schema_version", REQUEST_SPEC_VERSION)
    if payload["schema_version"] != REQUEST_SPEC_VERSION:
        raise ValueError(f"unsupported request spec version: {payload['schema_version']!r}")

    request = _mapping(payload.setdefault("request", {}))
    verbatim_prompt = _request_verbatim_from_mapping(request)
    if verbatim_prompt is None:
        verbatim_prompt = str(request.get("intent") or "")
    intent = str(request.get("intent") if request.get("intent") is not None else verbatim_prompt).strip()
    if not intent:
        raise ValueError("request.intent is required")
    request.setdefault("intent", intent)
    request.setdefault("verbatim_prompt", verbatim_prompt)
    request.setdefault("original_user_prompt", request.get("verbatim_prompt"))
    request.setdefault("protocol_name", None)
    request.setdefault("status", "draft")
    request.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    payload["request"] = request

    source = _mapping(payload.setdefault("source", {}))
    source.setdefault("context", None)
    source.setdefault("context_kind", None)
    source.setdefault("contexts", [])
    source.setdefault("project_archives", [])
    source.setdefault("collection", None)
    source.setdefault("source_scripts", [])
    source.setdefault("pattern_refs", [])
    source.setdefault("subroutine_dependencies", [])
    full_zeia = _mapping(source.setdefault("full_zeia_export", {}))
    full_zeia.setdefault("required", True)
    full_zeia.setdefault("status", "not_checked")
    full_zeia.setdefault("approved_partial_zeia", False)
    full_zeia.setdefault(
        "ask_user",
        "Provide a full FluentControl ZEIA export, or explicitly approve continuing with a partial/non-full export.",
    )
    source["full_zeia_export"] = full_zeia
    pattern_index = _mapping(source.setdefault("pattern_index", {}))
    pattern_index.setdefault("database", None)
    pattern_index.setdefault("pattern_ids", [])
    pattern_index.setdefault("pattern_queries", [])
    pattern_index.setdefault("source_script_rank", 1)
    source["pattern_index"] = pattern_index
    host_config = _mapping(source.setdefault("host_instrument_configuration", {}))
    host_config.setdefault("status", "unknown")
    host_config.setdefault("exact_names", [])
    host_config.setdefault("patterns", [])
    host_config.setdefault("required", False)
    host_config.setdefault(
        "ask_user",
        "Before import/run, verify the active FluentControl/VisionX instrument configuration matches the target instrument. If it does not, switch via the configuration dropdown.",
    )
    source["host_instrument_configuration"] = host_config
    payload["source"] = source

    generation = _mapping(payload.setdefault("generation", {}))
    options = generation_options_from_request_spec(payload)
    full_zeia["approved_partial_zeia"] = options.approve_partial_zeia
    generation.update(options.as_dict())
    generation.setdefault("allow_script_replacement", False)
    script_naming = _mapping(generation.setdefault("script_naming", {}))
    script_naming.setdefault("policy", "collision_safe_suffix")
    script_naming.setdefault("requested_name", None)
    script_naming.setdefault("final_name", None)
    script_naming.setdefault("renamed", False)
    script_naming.setdefault("allow_replacement", bool(generation.get("allow_script_replacement", False)))
    script_naming.setdefault("collisions", [])
    generation["script_naming"] = script_naming
    generation.setdefault("fluent_method", None)
    generation.setdefault("prompt_only", None)
    if "catalog_defaults" not in generation:
        generation["catalog_defaults"] = {}
    payload["generation"] = generation

    review = _mapping(payload.setdefault("review", {}))
    review.setdefault("state", "needs_review")
    review.setdefault("required_before_final_generation", True)
    review.setdefault("decisions", [])
    review.setdefault("unresolved_questions", [])
    review.setdefault("deck_layout", False)
    payload["review"] = review

    acceptance = _mapping(payload.setdefault("acceptance", {}))
    acceptance.setdefault("validation_diff_required", True)
    acceptance.setdefault("ready_to_import_bundle_required", True)
    acceptance.setdefault("ready_gates", "all_configured")
    required_checks = acceptance.get("required_checks")
    acceptance["required_checks"] = list(required_checks) if isinstance(required_checks, list) else []
    acceptance.setdefault("enforce_prompt_coverage", False)
    payload["acceptance"] = acceptance
    if "verification_recipe" in payload and payload["verification_recipe"] is not None:
        payload["verification_recipe"] = _normalize_verification_recipe(payload["verification_recipe"])
    return payload


def _normalize_verification_recipe(recipe: Any) -> dict[str, Any]:
    """Light normalization for the declarative verification recipe."""
    payload = _mapping(recipe)
    groups = payload.get("groups")
    payload["groups"] = list(groups) if isinstance(groups, list) else []
    labware = payload.get("labware")
    payload["labware"] = list(labware) if isinstance(labware, list) else []
    variables = payload.get("variables")
    payload["variables"] = list(variables) if isinstance(variables, list) else []
    simulation_values = payload.get("simulation_values") or payload.get("sim_values")
    payload["simulation_values"] = list(simulation_values) if isinstance(simulation_values, list) else []
    patterns = payload.get("worktable_patterns")
    payload["worktable_patterns"] = dict(patterns) if isinstance(patterns, dict) else {}
    init_name = str(payload.get("initialization_worktable") or "").strip()
    payload["initialization_worktable"] = init_name or None
    init_guid = str(payload.get("initialization_worktable_guid") or "").strip()
    payload["initialization_worktable_guid"] = init_guid or None
    tube_deck = str(payload.get("tube_deck_location") or "").strip().strip('"')
    payload["tube_deck_location"] = tube_deck or None
    prep = payload.get("capbc_prep")
    payload["capbc_prep"] = dict(prep) if isinstance(prep, dict) else {}
    prep_steps = payload.get("prep_steps")
    payload["prep_steps"] = list(prep_steps) if isinstance(prep_steps, list) else []
    selector = str(payload.get("category_selector_instructions") or "").strip()
    if selector:
        payload["category_selector_instructions"] = RUP_VARIABLE_SELECTOR_INSTRUCTIONS
    for group in payload["groups"]:
        if not isinstance(group, dict):
            continue
        steps = group.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            for key in ("prompt", "text"):
                if key in step and isinstance(step.get(key), str):
                    step[key] = _normalize_recipe_prompt_text(str(step.get(key)))
    return payload


RUP_VARIABLE_SELECTOR_INSTRUCTIONS = 'For each test, leave it on "yes" to run it or set it to "no" to skip it.'

_PRE_MOVEMENT_PROMPT_RE = re.compile(
    r"^\s*Next:\s*(?P<body>.*?)\s*"
    r"(?:Keep(?:\s+your)?\s+hands\s+clear\s+of\s+the\s+deck"
    r"(?:\s+and)?(?:\s*,?\s*then)?\s*)?"
    r"(?:press\s+OK(?:\s+to\s+continue)?\.?\s*)?$",
    re.IGNORECASE | re.DOTALL,
)
_HANDS_CLEAR_TRAILER_RE = re.compile(
    r"\s*Keep(?:\s+your)?\s+hands\s+clear\s+of\s+the\s+deck"
    r"(?:\s+and)?(?:\s*,?\s*then)?(?:\s+press\s+OK(?:\s+to\s+continue)?)?\.?\s*$",
    re.IGNORECASE,
)
_PRESS_OK_TRAILER_RE = re.compile(
    r"\s*(?:,?\s*then\s+)?press\s+OK(?:\s+to\s+continue)?\.?\s*$",
    re.IGNORECASE,
)
PRESS_CONTINUE_PROMPT = "Press Continue to proceed."
_PRESS_CONTINUE_TRAILER_RE = re.compile(
    rf"(?:\s*{re.escape(PRESS_CONTINUE_PROMPT)})+\s*$",
    re.IGNORECASE,
)
_OPERATOR_PROMPT_NUMBER_RE = re.compile(r"^\s*\d+\s*/\s*\d+\)\s*")


def recipe_worktable_patterns(recipe: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Named worktable labware bindings copied from mined source patterns."""
    if not isinstance(recipe, dict):
        return {}
    patterns = recipe.get("worktable_patterns")
    return dict(patterns) if isinstance(patterns, dict) else {}


def _recipe_labware_entry_by_label(
    labware_entries: list[dict[str, Any]],
    label: str,
) -> dict[str, Any] | None:
    wanted = str(label or "").strip()
    if not wanted:
        return None
    for entry in labware_entries:
        if str(entry.get("label") or "").strip() == wanted:
            return entry
    return None


def resolve_recipe_worktable_binding(
    raw_step: Mapping[str, Any],
    *,
    labware_entries: list[dict[str, Any]] | None = None,
    worktable_patterns: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Resolve an optional worktable binding for a recipe prompt step."""
    if not isinstance(raw_step, dict):
        return None
    binding = raw_step.get("worktable_binding")
    if binding is None:
        return None
    if isinstance(binding, str):
        key = binding.strip()
        resolved = (worktable_patterns or {}).get(key)
        if not isinstance(resolved, dict):
            return None
        binding = resolved
    if not isinstance(binding, dict):
        return None

    inherited: dict[str, Any] = {}
    from_label = str(binding.get("from_labware") or "").strip()
    if from_label:
        entry = _recipe_labware_entry_by_label(labware_entries or [], from_label)
        if entry:
            inherited["labware"] = from_label
            inherited["labware_type"] = str(entry.get("catalog") or "").strip()

    labware = str(binding.get("labware") or inherited.get("labware") or "").strip()
    labware_type = str(
        binding.get("labware_type")
        or binding.get("catalog")
        or inherited.get("labware_type")
        or ""
    ).strip()
    grid = binding.get("grid")
    site = binding.get("site")
    if not labware and grid is None and site is None:
        return None

    resolved_binding: dict[str, Any] = {
        "labware": labware,
        "labware_type": labware_type,
    }
    if grid is not None:
        resolved_binding["grid"] = int(grid)
    if site is not None:
        resolved_binding["site"] = int(site)
    return resolved_binding


_INTENT_CHECK_SIGNALS = (
    "make sure",
    "check",
    "verify",
    "ensure",
    "confirm",
    "did ",
    "does ",
    "move ",
    "moving ",
    "scan",
    "uncap",
    "recap",
    "cap ",
    "put ",
    "take",
    "taken",
    "close",
    "read",
    "place",
    "remove",
)


def extract_intent_checks(verbatim_prompt: str | None) -> list[str]:
    """Best-effort list of discrete requested checks from a free-text request."""
    checks: list[str] = []
    seen: set[str] = set()
    for raw in str(verbatim_prompt or "").splitlines():
        line = raw.strip().lstrip("-*\u2022").strip()
        if not line:
            continue
        low = line.casefold()
        words = line.split()
        is_question = line.endswith("?")
        has_signal = any(signal in low for signal in _INTENT_CHECK_SIGNALS)
        if not is_question and not has_signal and len(words) < 5:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", low).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        checks.append(line)
    return checks


def verification_recipe(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the verification recipe if the spec declares usable groups."""
    if not isinstance(spec, dict):
        return None
    recipe = spec.get("verification_recipe")
    if not isinstance(recipe, dict):
        return None
    groups = recipe.get("groups")
    if not isinstance(groups, list) or not groups:
        return None
    return recipe


def _normalize_recipe_prompt_text(text: str) -> str:
    """Normalize recipe prompt chrome only; keep ZEIA/operator/recipe wording.

    Strips step numbers and hands-clear / press-OK / Continue trailers, and
    lightly normalizes ``Next:`` pre-movement framing. Does **not** rewrite
    lab-specific setup nouns (A200, Falcon, cap holder, tube runner, etc.).
    """
    prompt = _OPERATOR_PROMPT_NUMBER_RE.sub("", str(text or "")).strip()
    prompt = _strip_press_continue_trailers(prompt)
    prompt = _strip_hands_clear_trailer(prompt)
    prompt = _normalize_pre_movement_prompt_text(prompt)
    prompt = _strip_hands_clear_trailer(prompt)
    prompt = _strip_press_continue_trailers(prompt)
    if prompt.casefold().startswith("next:"):
        return _with_press_continue(prompt)
    return prompt.strip()


def _strip_press_continue_trailers(prompt: str) -> str:
    original = str(prompt or "").strip()
    cleaned = _PRESS_CONTINUE_TRAILER_RE.sub("", original).strip()
    if cleaned != original:
        return cleaned.rstrip(".,;:").strip()
    return cleaned


def _with_press_continue(prompt: str) -> str:
    cleaned = _strip_press_continue_trailers(prompt).strip().rstrip(".")
    if not cleaned:
        return PRESS_CONTINUE_PROMPT
    return f"{cleaned}. {PRESS_CONTINUE_PROMPT}"


def _strip_hands_clear_trailer(prompt: str) -> str:
    """Remove hands-clear / press-OK trailers only; never append Continue."""
    original = str(prompt or "").strip()
    cleaned = _HANDS_CLEAR_TRAILER_RE.sub("", original).strip()
    cleaned = _PRESS_OK_TRAILER_RE.sub("", cleaned).strip()
    if cleaned != original:
        return cleaned.rstrip(".,;: ").strip()
    return cleaned


def _normalize_pre_movement_prompt_text(prompt: str) -> str:
    """Rewrite Next: movement prompts. Does not append Continue (caller does)."""
    match = _PRE_MOVEMENT_PROMPT_RE.match(str(prompt or ""))
    if not match:
        return prompt
    body = re.sub(r"\s+", " ", match.group("body")).strip().rstrip(".")
    body = _strip_press_continue_trailers(body)
    body = _strip_hands_clear_trailer(body)
    if not body:
        return "Next:"
    lowered = body.casefold()
    if lowered in {
        "the rga arm will present its fingers for inspection",
        "the arm will move to present its fingers for inspection",
    }:
        return "Next: the arm will move to present its fingers for inspection"
    if lowered == "the second tube will move up and be scanned by the second camera":
        return "Next: the second tube will move up and be scanned by the second camera"
    arm_prefix = "the arm will "
    if lowered.startswith(arm_prefix):
        action = body[len(arm_prefix) :].strip()
        action = re.sub(r"\bto its home nest\b", "to its original position", action, flags=re.I)
        return f"Next: the arm will {action}"
    rga_prefix = "the rga arm will "
    if lowered.startswith(rga_prefix):
        action = body[len(rga_prefix) :].strip()
        return f"Next: the RGA arm will {action}"
    return f"Next: {body}"


def _sentence_with_for_you(text: str) -> str:
    sentence = re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".")
    if not sentence:
        return ""
    if not sentence.casefold().endswith(" for you"):
        sentence = f"{sentence} for you"
    return f"{sentence}."


def request_spec_generation_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    """Return generation CLI defaults carried by a request spec."""
    payload = normalize_request_spec(spec)
    source = payload["source"]
    pattern_index = source.get("pattern_index") or {}
    options = generation_options_from_request_spec(payload)
    generation = payload["generation"]
    source_context = source.get("context")
    source_collection = source.get("collection")
    if source.get("context_kind") == "project_collection":
        source_collection = source_collection or source_context
        source_context = None
    return {
        "intent": request_verbatim_prompt(payload),
        "protocol_name": payload["request"].get("protocol_name"),
        "project_archives": [Path(path) for path in source.get("project_archives") or []],
        "context": source_context,
        "contexts": [
            str(item.get("name") or "")
            for item in (source.get("contexts") or [])
            if isinstance(item, dict) and item.get("name")
        ],
        "collection": source_collection,
        "source_scripts": list(source.get("source_scripts") or []),
        "pattern_refs": list(source.get("pattern_refs") or []),
        "index_db": Path(pattern_index["database"]) if pattern_index.get("database") else None,
        "pattern_ids": list(pattern_index.get("pattern_ids") or []),
        "pattern_queries": list(pattern_index.get("pattern_queries") or []),
        "source_script_rank": int(pattern_index.get("source_script_rank") or 1),
        "generation_options": options,
        "simulate": options.simulate,
        "compile_xscr": options.compile_xscr,
        "max_repair_iterations": options.max_repair_iterations,
        "strict_readiness": options.strict_readiness,
        "apply_modeling": options.apply_modeling,
        "approve_partial_zeia": options.approve_partial_zeia,
        "approve_deck_layout": options.approve_deck_layout,
        "approve_command_inventory": options.approve_command_inventory,
        "approve_unsupported_raw_xml": options.approve_unsupported_raw_xml,
        "approved_unsupported_command_ids": list(options.approved_unsupported_command_ids),
        "waive_checksum_recompute": options.waive_checksum_recompute,
        "preserve_regeneration_baseline": options.preserve_regeneration_baseline,
        "fluent_context_check": options.fluent_context_check,
        "fluent_provider": options.fluent_provider,
        "fluent_timeout": options.fluent_timeout,
        "allow_script_replacement": bool(generation.get("allow_script_replacement", False)),
        "fluent_method": generation.get("fluent_method"),
        "catalog_defaults": dict(generation.get("catalog_defaults") or {}),
        "host_instrument_configuration": dict(source.get("host_instrument_configuration") or {}),
    }


def build_request_validation_diff(
    *,
    request_spec: dict[str, Any],
    protocol_ir: dict[str, Any],
    request_spec_path: Path | None,
    protocol_ir_path: Path | None,
    generated_files: dict[str, str],
    worktable_diff: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
    fluent_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare the reviewed request contract with generated workflow outputs."""
    spec = normalize_request_spec(request_spec)
    request = spec["request"]
    verbatim_prompt = request_verbatim_prompt(spec)
    intent_summary = str(request.get("intent") or "")
    source = spec["source"]
    generation = spec["generation"]
    review = spec["review"]
    ir_source = protocol_ir.get("source") or {}
    protocol = protocol_ir.get("protocol") or {}
    declared_prompt_only = generation.get("prompt_only")
    prompt_only = bool(declared_prompt_only) if declared_prompt_only is not None else protocol_is_prompt_only(protocol_ir)
    checks = [
        _check(
            "request_spec_present",
            "passed",
            "request.spec.yaml was captured for this workflow.",
            {"path": str(request_spec_path) if request_spec_path else None},
        ),
        _check_intent(verbatim_prompt or intent_summary, protocol_ir),
        _check_source_selection(source, ir_source),
        _check_full_zeia_export(source),
        _check_host_instrument_configuration(source, ir_source),
        _check_subroutine_dependencies(source, protocol_ir),
        _check_generated_artifacts(generated_files),
        _check_worktable_diff(worktable_diff),
        _check_ready_validation(validation_report, prompt_only=prompt_only),
        _check_runtime_variable_audit(protocol_ir, fluent_report),
        _check_query_variable_audit(protocol_ir, spec, fluent_report),
        _check_review_state(review),
    ]
    return {
        "kind": "request_validation_diff",
        "schema_version": REQUEST_VALIDATION_DIFF_VERSION,
        "request_spec": {
            "path": str(request_spec_path) if request_spec_path else None,
            "intent": intent_summary,
            "intent_summary": intent_summary,
            "verbatim_prompt": verbatim_prompt,
            "protocol_name": request.get("protocol_name"),
            "status": request.get("status"),
            "review_state": review.get("state"),
        },
        "protocol_ir": {
            "path": str(protocol_ir_path) if protocol_ir_path else None,
            "id": protocol_ir.get("id"),
            "name": protocol.get("name"),
            "step_count": len(protocol_ir.get("steps") or []),
            "labware_count": len(protocol_ir.get("labware") or []),
            "liquid_class_count": len(protocol_ir.get("liquid_classes") or []),
        },
        "generated_files": dict(generated_files),
        "checks": checks,
        "status": _overall_status(checks),
    }


def render_request_validation_diff_markdown(diff: dict[str, Any]) -> str:
    """Render a compact Markdown review artifact."""
    spec = diff.get("request_spec") or {}
    ir = diff.get("protocol_ir") or {}
    verbatim_prompt = str(spec.get("verbatim_prompt") or "")
    intent_summary = str(spec.get("intent_summary") or spec.get("intent") or "")
    lines = [
        "# Request Validation Diff",
        "",
        f"- Status: `{diff.get('status')}`",
        f"- Request spec: `{spec.get('path') or 'not recorded'}`",
        "- Original request prompt: recorded" if verbatim_prompt else "- Original request prompt: not recorded",
        f"- Protocol IR: `{ir.get('path') or 'not recorded'}`",
        f"- Protocol name: `{ir.get('name') or ''}`",
        f"- Steps: `{ir.get('step_count', 0)}`",
        f"- Labware: `{ir.get('labware_count', 0)}`",
        f"- Liquid classes: `{ir.get('liquid_class_count', 0)}`",
        "",
    ]
    if verbatim_prompt:
        lines.extend(["```text", verbatim_prompt, "```", ""])
    if intent_summary and intent_summary != verbatim_prompt:
        lines.extend([f"- Intent summary: {intent_summary}", ""])
    lines.extend(["## Checks", ""])
    for check in diff.get("checks") or []:
        lines.append(f"- `{check.get('status')}` {check.get('id')}: {check.get('summary')}")
        details = check.get("details") or {}
        for key, value in details.items():
            if value in (None, "", [], {}):
                continue
            rendered = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            lines.append(f"  - {key}: `{rendered}`")
    runtime_audit_check = next(
        (check for check in diff.get("checks") or [] if check.get("id") == "runtime_variable_audit"),
        None,
    )
    if runtime_audit_check:
        lines.extend(
            [
                "",
                render_runtime_variable_audit_markdown(
                    {
                        **(runtime_audit_check.get("details") or {}),
                        "status": runtime_audit_check.get("status"),
                        "summary": runtime_audit_check.get("summary"),
                    }
                ),
            ]
        )
    query_audit_check = next(
        (check for check in diff.get("checks") or [] if check.get("id") == "query_variable_runtime_audit"),
        None,
    )
    if query_audit_check:
        lines.extend(
            [
                "",
                render_query_variable_audit_markdown(
                    {
                        **(query_audit_check.get("details") or {}),
                        "status": query_audit_check.get("status"),
                        "summary": query_audit_check.get("summary"),
                    }
                ),
            ]
        )
    lines.extend(["", "## Generated Files", ""])
    for key, value in sorted((diff.get("generated_files") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines).rstrip() + "\n"


def request_verbatim_prompt(spec: dict[str, Any] | None) -> str:
    """Return the exact user prompt recorded by the request spec."""
    if not isinstance(spec, dict):
        return ""
    request = spec.get("request") if isinstance(spec.get("request"), dict) else spec
    if not isinstance(request, dict):
        return ""
    value = _request_verbatim_from_mapping(request)
    if value is not None:
        return value
    return str(request.get("intent") or "")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _request_verbatim_from_mapping(request: dict[str, Any]) -> str | None:
    for key in ("verbatim_prompt", "original_user_prompt", "source_prompt", "prompt"):
        if key in request and request.get(key) is not None:
            return str(request.get(key))
    return None


def _check(check_id: str, status: str, summary: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "summary": summary,
        "details": details or {},
    }


def _check_intent(intent: str | None, protocol_ir: dict[str, Any]) -> dict[str, Any]:
    ir_source = protocol_ir.get("source") or {}
    ir_request_spec = ir_source.get("request_spec") if isinstance(ir_source.get("request_spec"), dict) else {}
    haystack = [
        str((protocol_ir.get("protocol") or {}).get("comment") or ""),
        str(ir_source.get("intent") or ""),
        str(ir_source.get("generation_intent") or ""),
        str(ir_source.get("verbatim_prompt") or ""),
        str(ir_source.get("original_user_prompt") or ""),
        str(ir_request_spec.get("verbatim_prompt") or ""),
        str(ir_request_spec.get("original_user_prompt") or ""),
    ]
    if intent and any(intent in value for value in haystack):
        return _check("intent_carried_to_ir", "passed", "The request intent is present in protocol IR metadata.")
    return _check("intent_carried_to_ir", "needs_user", "The protocol IR does not clearly preserve the request intent.")


def _check_source_selection(source: dict[str, Any], ir_source: dict[str, Any]) -> dict[str, Any]:
    requested_scripts = [str(value) for value in (source.get("source_scripts") or [])]
    ir_scripts = ir_source.get("selected_source_scripts") or []
    requested_patterns = [str(value) for value in (source.get("pattern_refs") or [])]
    ir_patterns = [str(value) for value in (ir_source.get("selected_patterns") or [])]
    if not requested_scripts and not requested_patterns:
        return _check("source_selection_carried_to_ir", "needs_user", "No source scripts or reusable patterns were requested.")
    missing_patterns = [value for value in requested_patterns if value not in ir_patterns]
    if missing_patterns:
        return _check(
            "source_selection_carried_to_ir",
            "needs_user",
            "Some requested pattern references are not visible in the IR metadata.",
            {"missing_patterns": missing_patterns},
        )
    if len(ir_scripts) >= len(requested_scripts):
        return _check("source_selection_carried_to_ir", "passed", "Requested sources are represented in IR metadata.")
    return _check(
        "source_selection_carried_to_ir",
        "needs_user",
        "Some requested source scripts are not visible in IR metadata.",
        {"requested_source_scripts": requested_scripts, "ir_source_script_count": len(ir_scripts)},
    )


def _check_full_zeia_export(source: dict[str, Any]) -> dict[str, Any]:
    assessment = source.get("full_zeia_export") or {}
    if not isinstance(assessment, dict):
        assessment = {}
    approved = bool(
        assessment.get("approved_partial_zeia")
        or assessment.get("explicit_partial_zeia_approval")
    )
    accepted = bool(assessment.get("accepted"))
    status = str(assessment.get("status") or "not_checked")
    if accepted and status == "likely_full_export":
        return _check("full_zeia_export_required", "passed", "The source context appears to be a full ZEIA export.")
    if approved:
        return _check(
            "full_zeia_export_required",
            "passed",
            "The source context did not clearly appear to be a full ZEIA export, but explicit partial-ZEIA approval was recorded.",
            {"status": status, "blocking_findings": assessment.get("blocking_findings") or []},
        )
    return _check(
        "full_zeia_export_required",
        "needs_user",
        "A full ZEIA export is required before generation continues, unless the user explicitly approves the partial/non-full export.",
        {
            "status": status,
            "ask_user": assessment.get("ask_user"),
            "blocking_findings": assessment.get("blocking_findings") or [],
        },
    )


def _check_host_instrument_configuration(source: dict[str, Any], ir_source: dict[str, Any]) -> dict[str, Any]:
    spec_hint = source.get("host_instrument_configuration") or {}
    ir_report = ir_source.get("host_instrument_configuration") or {}
    if not isinstance(spec_hint, dict):
        spec_hint = {}
    if not isinstance(ir_report, dict):
        ir_report = {}
    status = str(ir_report.get("status") or spec_hint.get("status") or "unknown")
    if status == "matched":
        return _check(
            "host_instrument_configuration",
            "passed",
            "An installed FluentControl/VisionX host configuration matches the expected hint.",
            {"matches": ir_report.get("matches") or []},
        )
    if spec_hint.get("exact_names") or spec_hint.get("patterns") or ir_report.get("installed_configs"):
        return _check(
            "host_instrument_configuration",
            "needs_user",
            "Verify FluentControl is using the expected host instrument configuration before import/run.",
            {
                "status": status,
                "expected": spec_hint,
                "installed_configs": ir_report.get("installed_configs") or [],
                "instruction": ir_report.get("user_instruction") or spec_hint.get("ask_user"),
            },
        )
    return _check(
        "host_instrument_configuration",
        "needs_user",
        "No expected host instrument configuration was inferred; operator confirmation is required.",
    )


def _check_subroutine_dependencies(source: dict[str, Any], protocol_ir: dict[str, Any]) -> dict[str, Any]:
    expected = [str(item) for item in (source.get("subroutine_dependencies") or []) if item not in (None, "")]
    actual = []
    for step in protocol_ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        value = str(params.get("subroutine") or params.get("SubRoutine") or "").strip().strip('"')
        if value:
            actual.append(value)
    if not expected and not actual:
        return _check("subroutine_dependencies", "passed", "No subroutine calls were requested or generated.")
    missing = [value for value in expected if value not in actual]
    if missing:
        return _check(
            "subroutine_dependencies",
            "needs_user",
            "Some request-spec subroutine dependencies are not visible in the protocol IR.",
            {"missing": missing, "generated_subroutines": actual},
        )
    return _check(
        "subroutine_dependencies",
        "passed",
        f"Protocol IR records {len(actual)} subroutine call dependency(ies).",
        {"generated_subroutines": actual},
    )


def _check_generated_artifacts(generated_files: dict[str, str]) -> dict[str, Any]:
    required = {"ir", "python", "recreate", "worktable_changes", "worktable_patch"}
    missing = sorted(required.difference(generated_files))
    if missing:
        return _check("generated_artifacts_present", "failed", "Some required generated artifacts are missing.", {"missing": missing})
    return _check("generated_artifacts_present", "passed", "Required generated artifacts are present.")


def _check_worktable_diff(worktable_diff: dict[str, Any] | None) -> dict[str, Any]:
    if not worktable_diff:
        return _check("worktable_diff_present", "failed", "No worktable diff was produced.")
    severity = ((worktable_diff.get("summary") or {}).get("overall_severity") or "unknown")
    status = "passed" if severity in {"safe", "needs_review"} else "needs_user"
    return _check("worktable_diff_present", status, f"Worktable diff overall severity is `{severity}`.")


def _check_ready_validation(
    validation_report: dict[str, Any] | None,
    *,
    prompt_only: bool = False,
) -> dict[str, Any]:
    if validation_report is None:
        return _check("ready_validation", "needs_user", "Ready validation did not run because no compiled XSCR was available.")
    if validation_report.get("ready"):
        trivial = validation_report.get("trivial_pass_gates") or []
        if trivial:
            if prompt_only:
                return _check(
                    "ready_validation",
                    "passed",
                    "All ready gates passed; "
                    f"{len(trivial)} passed trivially with nothing to check "
                    f"({', '.join(trivial)}). Expected: the spec declares this protocol "
                    "prompt-only (no liquid handling), so an empty liquid-handling result "
                    "is intended.",
                    {"trivial_pass_gates": trivial, "prompt_only": True},
                )
            return _check(
                "ready_validation",
                "passed",
                "All ready gates passed; "
                f"{len(trivial)} passed trivially with nothing to check "
                f"({', '.join(trivial)}). Confirm an empty result matches intent.",
                {"trivial_pass_gates": trivial},
            )
        return _check("ready_validation", "passed", "All ready gates passed.")
    return _check(
        "ready_validation",
        "failed",
        "One or more ready gates failed.",
        {
            "passed_count": validation_report.get("passed_count"),
            "gate_count": validation_report.get("gate_count"),
            "failed_count": validation_report.get("failed_count"),
            "failed_gates": [
                {
                    "gate": gate.get("gate"),
                    "id": gate.get("id"),
                    "name": gate.get("name"),
                    "summary": gate.get("summary"),
                    "details": gate.get("details"),
                }
                for gate in (validation_report.get("gates") or [])
                if gate.get("status") == "failed"
            ],
        },
    )


def _check_review_state(review: dict[str, Any]) -> dict[str, Any]:
    state = str(review.get("state") or "").lower()
    if state in {"approved", "reviewed"}:
        return _check("request_review_state", "passed", f"Request spec review state is `{state}`.")
    return _check("request_review_state", "needs_user", f"Request spec review state is `{state or 'unknown'}`.")


def _check_runtime_variable_audit(
    protocol_ir: dict[str, Any],
    fluent_report: dict[str, Any] | None,
) -> dict[str, Any]:
    audit = audit_runtime_variables_for_workflow(
        protocol_ir=protocol_ir,
        fluent_report=fluent_report,
    )
    return validation_diff_check_for_runtime_audit(audit)


def _check_query_variable_audit(
    protocol_ir: dict[str, Any],
    request_spec: dict[str, Any],
    fluent_report: dict[str, Any] | None,
) -> dict[str, Any]:
    audit = audit_query_variables_for_workflow(
        protocol_ir=protocol_ir,
        request_spec=request_spec,
        fluent_report=fluent_report,
    )
    return validation_diff_check_for_query_audit(audit)


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = [check.get("status") for check in checks]
    if "failed" in statuses:
        return "failed"
    if "needs_user" in statuses or "needs-review" in statuses:
        return "needs_user"
    return "passed"


def _yaml_module(*, required: bool = True) -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        if not required:
            return None
        raise ValueError("request.spec.yaml requires PyYAML. Use .json output or install PyYAML.") from exc
    return yaml


def _dump_simple_yaml(payload: dict[str, Any], *, indent: int = 0) -> str:
    """Write the request-spec subset without requiring PyYAML."""
    lines: list[str] = []
    prefix = " " * indent
    for key, value in payload.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_dump_simple_yaml(value, indent=indent + 2).rstrip())
        else:
            lines.append(f"{prefix}{key}: {_simple_yaml_scalar(value)}")
    return "\n".join(lines).rstrip() + "\n"


def _simple_yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    text = str(value)
    if text and all(char.isalnum() or char in " ._:/@+-" for char in text):
        lower = text.lower()
        if lower not in {"null", "true", "false"}:
            return text
    return json.dumps(text)


def _load_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        while indent <= stack[-1][0] and len(stack) > 1:
            stack.pop()
        parent = stack[-1][1]
        if raw_value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_simple_yaml_scalar(raw_value)
    return root


def _parse_simple_yaml_scalar(raw_value: str) -> Any:
    if raw_value == "null":
        return None
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    if raw_value.startswith(("[", "{", '"')):
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value.strip('"')
    try:
        return int(raw_value)
    except ValueError:
        pass
    try:
        return float(raw_value)
    except ValueError:
        return raw_value
