"""Interactive operator scripts: Query Variable steps and declarations."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from .protocol_ir import normalize_operator_prompt_text
from .query_variable_audit import _is_variable_name

_INTERACTIVE_INTENT_PATTERNS = (
    re.compile(r"\binteract(?:ive|ion)?\b", re.I),
    re.compile(r"\buser\s+input\b", re.I),
    re.compile(r"\basks?\s+(?:the\s+)?(?:user|operator)\b", re.I),
    re.compile(r"\boperator\s+(?:input|question|prompt)\b", re.I),
    re.compile(r"\bquery\s+variable\b", re.I),
    re.compile(r"\benter\s+(?:a\s+)?(?:value|number|count)\b", re.I),
    re.compile(r"\bquestions?\b", re.I),
    re.compile(r"\bcollect\s+(?:the\s+)?(?:user|operator)\b", re.I),
    re.compile(r"\bform\s+(?:for|to)\s+(?:the\s+)?(?:user|operator)\b", re.I),
)

_VERIFICATION_MEDIA_PROMPT_KEYS = frozenset(
    {
        "deck_presence_check",
        "instrument_init_check",
        "worktable_binding",
        "plain_prompt",
    }
)


def request_wants_interactive_script(
    request_spec: Mapping[str, Any] | None,
    *,
    intent: str = "",
) -> bool:
    """True when the request should use Query Variable steps instead of User Prompts."""
    spec = dict(request_spec or {})
    generation = spec.get("generation") if isinstance(spec.get("generation"), dict) else {}
    if generation.get("interactive") is True:
        return True
    text = " ".join(
        part
        for part in (
            str(intent or "").strip(),
            str((spec.get("request") or {}).get("intent") or "").strip()
            if isinstance(spec.get("request"), dict)
            else "",
            str(spec.get("intent") or "").strip(),
        )
        if part
    )
    if not text:
        return False
    return any(pattern.search(text) for pattern in _INTERACTIVE_INTENT_PATTERNS)


def prepare_interactive_recipe(
    recipe: dict[str, Any],
    *,
    request_spec: Mapping[str, Any] | None = None,
    intent: str = "",
) -> dict[str, Any]:
    """Rewrite non-media recipe prompts as ``query_variable`` steps when appropriate."""
    if not request_wants_interactive_script(request_spec, intent=intent):
        return recipe
    payload = copy.deepcopy(recipe)
    variables = list(payload.get("variables") or [])
    known_names = {
        str(item.get("name") or "").strip()
        for item in variables
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    for group in payload.get("groups") or []:
        if not isinstance(group, dict):
            continue
        steps = group.get("steps")
        if not isinstance(steps, list):
            continue
        group["steps"] = [
            _promote_prompt_step_to_query(step, variables=variables, known_names=known_names)
            for step in steps
        ]
    payload["variables"] = variables
    return payload


def ensure_recipe_variable_declaration(
    variables: list[dict[str, Any]],
    *,
    name: str,
    prompt: str = "",
    minimum: Any = None,
    maximum: Any = None,
    default_value: Any = None,
    variable_type: str = "double",
) -> None:
    """Add or update one FluentControl variable record for a query step."""
    clean_name = str(name or "").strip()
    if not _is_variable_name(clean_name):
        return
    for record in variables:
        if not isinstance(record, dict):
            continue
        if str(record.get("name") or "").strip() != clean_name:
            continue
        if prompt and not str(record.get("query_prompt") or "").strip():
            record["query_prompt"] = prompt
        if minimum is not None and "minimum" not in record:
            record["minimum"] = minimum
        if maximum is not None and "maximum" not in record:
            record["maximum"] = maximum
        if default_value is not None and "default_value" not in record:
            record["default_value"] = default_value
        return
    entry: dict[str, Any] = {
        "name": clean_name,
        "type": variable_type,
        "scope": "Global",
    }
    if prompt:
        entry["query_prompt"] = prompt
    if default_value is not None:
        entry["default_value"] = default_value
    if minimum is not None:
        entry["minimum"] = minimum
    if maximum is not None:
        entry["maximum"] = maximum
    variables.append(entry)


def variable_name_from_question(prompt: str, *, fallback: str) -> str:
    """Derive a PascalCase variable name from operator question text."""
    words = re.findall(r"[A-Za-z0-9]+", str(prompt or ""))
    if not words:
        return fallback
    name = "".join(word[:1].upper() + word[1:] for word in words[:6])
    if not _is_variable_name(name):
        return fallback
    return name


def _promote_prompt_step_to_query(
    raw_step: Any,
    *,
    variables: list[dict[str, Any]],
    known_names: set[str],
) -> Any:
    if not isinstance(raw_step, dict):
        return raw_step
    from .request_spec import recipe_step_type

    step_type = recipe_step_type(raw_step)
    if step_type != "prompt":
        return raw_step
    if any(key in raw_step for key in _VERIFICATION_MEDIA_PROMPT_KEYS):
        return raw_step
    prompt = normalize_operator_prompt_text(str(raw_step.get("prompt") or raw_step.get("text") or ""))
    if not prompt or not _prompt_collects_variable_value(prompt):
        return raw_step
    variable = str(raw_step.get("variable") or "").strip()
    if not variable or not _is_variable_name(variable):
        variable = variable_name_from_question(prompt, fallback="OperatorInput")
        suffix = 2
        while variable in known_names:
            variable = f"{variable_name_from_question(prompt, fallback='OperatorInput')}{suffix}"
            suffix += 1
    known_names.add(variable)
    ensure_recipe_variable_declaration(
        variables,
        name=variable,
        prompt=prompt,
        minimum=raw_step.get("minimum"),
        maximum=raw_step.get("maximum"),
        default_value=raw_step.get("default_value"),
        variable_type=str(raw_step.get("type") or raw_step.get("variable_type") or "double"),
    )
    query_step: dict[str, Any] = {
        "type": "query_variable",
        "variable": variable,
        "prompt": prompt,
    }
    for key in ("minimum", "maximum", "timeout", "default_value"):
        if key in raw_step:
            query_step[key] = raw_step[key]
    return query_step


def _prompt_collects_variable_value(prompt: str) -> bool:
    text = str(prompt or "").strip()
    if not text:
        return False
    if text.endswith("?"):
        return True
    lowered = text.casefold()
    starters = (
        "how many",
        "how much",
        "enter ",
        "select ",
        "choose ",
        "pick ",
        "what is",
        "what's",
        "provide ",
        "type ",
        "input ",
    )
    return any(lowered.startswith(prefix) for prefix in starters)
