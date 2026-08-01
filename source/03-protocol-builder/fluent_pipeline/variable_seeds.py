"""Collect SetVariableValue seeds for Gate 27 fluent context checks (api-v2-034)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any, Mapping


def collect_variable_seeds(
    *,
    protocol_ir: Mapping[str, Any] | None = None,
    request_spec: Mapping[str, Any] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Merge IR and request.spec seeds for ``RuntimeController.SetVariableValue``."""
    merged: dict[str, str] = {}
    if request_spec:
        _merge_seeds(merged, _seeds_from_request_spec(request_spec))
    if protocol_ir:
        _merge_seeds(merged, _seeds_from_protocol_ir(protocol_ir))
    return tuple(sorted(merged.items()))


def variable_seeds_as_json(seeds: Sequence[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str:
    payload = [{"name": name, "value": value} for name, value in seeds]
    return json.dumps(payload, separators=(",", ":"))


def apply_variable_seeds_offline(
    seeds: Sequence[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    close_query_dialog: bool = True,
) -> dict[str, Any]:
    """Offline scaffold for api-v2-034 SetVariableValue before PrepareMethod."""
    if not seeds:
        return {
            "variable_seeds_applied": [],
            "variable_seed_ok": True,
            "variable_seed_errors": [],
            "seeded_variables": {},
        }
    from .api_v2.runtime import MockRuntimeController, seed_simulation_values
    from .api_v2.types import VariableSeed

    runtime = MockRuntimeController()
    variable_seeds = [VariableSeed(name=name, value=value) for name, value in seeds]
    ok, errors = seed_simulation_values(runtime, variable_seeds)
    if close_query_dialog:
        runtime.close_query_at_startup_dialog(True)
    return {
        "variable_seeds_applied": [name for name, _ in seeds],
        "variable_seed_ok": ok,
        "variable_seed_errors": errors,
        "seeded_variables": dict(runtime.variables),
    }


def _merge_seeds(target: dict[str, str], seeds: Iterable[tuple[str, str]]) -> None:
    for name, value in seeds:
        key = str(name or "").strip()
        if not key:
            continue
        target[key] = str(value)


def _seeds_from_request_spec(request_spec: Mapping[str, Any]) -> list[tuple[str, str]]:
    spec = dict(request_spec)
    seeds: list[tuple[str, str]] = []
    recipe = spec.get("verification_recipe") if isinstance(spec.get("verification_recipe"), dict) else {}
    for record in recipe.get("simulation_values") or []:
        seeds.extend(_simulation_value_record(record))
    for record in spec.get("simulation_values") or []:
        seeds.extend(_simulation_value_record(record))
    generation = spec.get("generation") if isinstance(spec.get("generation"), dict) else {}
    for record in generation.get("simulation_values") or []:
        seeds.extend(_simulation_value_record(record))
    return seeds


def _seeds_from_protocol_ir(protocol_ir: Mapping[str, Any]) -> list[tuple[str, str]]:
    seeds: list[tuple[str, str]] = []
    for record in protocol_ir.get("simulation_values") or protocol_ir.get("sim_values") or []:
        seeds.extend(_simulation_value_record(record))
    for record in protocol_ir.get("variables") or []:
        seeds.extend(_query_at_startup_record(record))
    for step in protocol_ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("operation") or "") != "query_variable":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        name = str(params.get("variable") or "").strip()
        if not name:
            continue
        default = _variable_default_from_record(params)
        if default is not None:
            seeds.append((name, _format_seed_value(default)))
    return seeds


def _simulation_value_record(record: Any) -> list[tuple[str, str]]:
    if not isinstance(record, dict):
        return []
    name = str(record.get("name") or record.get("expression") or record.get("variable") or "").strip()
    if not name:
        return []
    value = record.get("value")
    if value is None and "sim_value" in record:
        value = record.get("sim_value")
    if value is None:
        return []
    return [(name, _format_seed_value(value))]


def _query_at_startup_record(record: Any) -> list[tuple[str, str]]:
    if not isinstance(record, dict):
        return []
    name = str(record.get("name") or "").strip()
    if not name:
        return []
    if not (_coerce_bool(record.get("query_at_startup")) or record.get("queries")):
        return []
    default = _variable_default_from_record(record)
    if default is None:
        return []
    return [(name, _format_seed_value(default))]


def _variable_default_from_record(record: Mapping[str, Any]) -> Any:
    for key in ("default_value", "value"):
        if _has_value(record.get(key)):
            return record[key]
    values = record.get("values") or record.get("default_values")
    if isinstance(values, list) and values:
        return values[0]
    return None


def _format_seed_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}
