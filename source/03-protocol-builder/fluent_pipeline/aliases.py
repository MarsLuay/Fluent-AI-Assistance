"""Alias maps for FluentControl catalog and source-context normalization."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .config import PROJECT_DIR
from .fluent_naming import strip_fluent_instance_suffix

DEFAULT_ALIAS_DIR = PROJECT_DIR / "config" / "aliases"
ALIAS_FILES = {
    "catalog_aliases": "catalog_aliases.yaml",
    "labware_aliases": "labware_aliases.yaml",
    "liquid_class_aliases": "liquid_class_aliases.yaml",
    "device_aliases": "device_aliases.yaml",
}
ALIAS_KINDS = tuple(ALIAS_FILES)
KIND_ALIASES = {
    "catalog": ("catalog_aliases",),
    "catalog_alias": ("catalog_aliases",),
    "catalog_aliases": ("catalog_aliases",),
    "carrier": ("catalog_aliases",),
    "rack_type": ("catalog_aliases",),
    "labware": ("labware_aliases",),
    "labware_alias": ("labware_aliases",),
    "labware_aliases": ("labware_aliases",),
    "liquid_class": ("liquid_class_aliases",),
    "liquid_classes": ("liquid_class_aliases",),
    "liquid_class_alias": ("liquid_class_aliases",),
    "liquid_class_aliases": ("liquid_class_aliases",),
    "device": ("device_aliases",),
    "device_alias": ("device_aliases",),
    "device_aliases": ("device_aliases",),
}


def load_alias_maps(alias_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """Load configured alias maps from YAML files."""
    base = alias_dir or DEFAULT_ALIAS_DIR
    maps = {kind: {} for kind in ALIAS_KINDS}
    for kind, filename in ALIAS_FILES.items():
        path = base / filename
        if not path.exists():
            continue
        payload = _load_yamlish(path)
        mapping = payload.get(kind) if isinstance(payload.get(kind), dict) else payload
        maps[kind].update(_string_mapping(mapping))
    return maps


def alias_records(alias_maps: dict[str, dict[str, str]] | None = None) -> list[dict[str, str]]:
    """Return aliases as stable, CLI-friendly records."""
    maps = alias_maps if alias_maps is not None else load_alias_maps()
    records = []
    for kind in ALIAS_KINDS:
        for alias, canonical in sorted(maps.get(kind, {}).items(), key=lambda item: item[0].casefold()):
            records.append({"kind": kind, "alias": alias, "canonical": canonical})
    return records


def resolve_alias(
    value: Any,
    kind: str,
    alias_maps: dict[str, dict[str, str]] | None = None,
) -> str:
    """Resolve one alias value for a named alias kind.

    For labware/catalog kinds, Fluent instance suffixes such as ``[001]`` are
    stripped automatically when no explicit alias map entry matches. Site-specific
    type names must come from the imported ZEIA / ``labware_catalog.json``, not
    from hardcoded product maps.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    maps = alias_maps if alias_maps is not None else load_alias_maps()
    keys = _map_keys(kind)
    current = text
    seen = set()
    for _ in range(10):
        if current in seen:
            break
        seen.add(current)
        replacement = _lookup_alias(current, keys, maps)
        if not replacement or replacement == current:
            bare = _strip_instance_suffix(current)
            if bare != current:
                bare_hit = _lookup_alias(bare, keys, maps)
                if bare_hit and bare_hit != current:
                    current = bare_hit
                    continue
                if _allows_instance_strip(kind):
                    current = bare
                    continue
            break
        current = replacement
    return current


def merge_alias_maps(
    *maps: dict[str, dict[str, str]] | None,
) -> dict[str, dict[str, str]]:
    """Merge alias map dicts; later maps override earlier keys."""
    merged = {kind: {} for kind in ALIAS_KINDS}
    for candidate in maps:
        if not candidate:
            continue
        for kind in ALIAS_KINDS:
            mapping = candidate.get(kind)
            if isinstance(mapping, dict):
                merged[kind].update(_string_mapping(mapping))
    return merged


def load_alias_maps_with_context_catalog(
    alias_dir: Path | None = None,
    *,
    labware_catalog_path: Path | None = None,
    liquid_classes_path: Path | None = None,
) -> dict[str, dict[str, str]]:
    """Load shipped alias YAML, then overlay ZEIA-derived catalogs."""
    maps = load_alias_maps(alias_dir)
    if labware_catalog_path is not None:
        from .labware_catalog_export import alias_maps_from_labware_catalog, load_labware_catalog

        catalog = load_labware_catalog(labware_catalog_path)
        if catalog:
            maps = merge_alias_maps(maps, alias_maps_from_labware_catalog(catalog))
    if liquid_classes_path is not None:
        from .liquid_classes_export import (
            alias_maps_from_liquid_classes_catalog,
            load_liquid_classes_catalog,
        )

        liquid = load_liquid_classes_catalog(liquid_classes_path)
        if liquid:
            maps = merge_alias_maps(maps, alias_maps_from_liquid_classes_catalog(liquid))
    return maps


def alias_candidates(
    value: Any,
    kind: str,
    alias_maps: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Return the original value followed by its resolved alias, if different."""
    text = str(value or "").strip()
    if not text:
        return []
    resolved = resolve_alias(text, kind, alias_maps)
    values = [text]
    if resolved and _norm(resolved) != _norm(text):
        values.append(resolved)
    return values


def normalize_protocol_ir_aliases(
    protocol_ir: dict[str, Any],
    alias_maps: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return a copy of protocol IR with configured aliases normalized."""
    maps = alias_maps if alias_maps is not None else load_alias_maps()
    ir = copy.deepcopy(protocol_ir)

    for item in ir.get("labware") or []:
        if not isinstance(item, dict):
            continue
        _normalize_field(item, "label", "labware", maps)
        for key in ("catalog", "labware_type", "rack_type", "carrier", "carrier_type", "forced_rack_type"):
            _normalize_field(item, key, "catalog", maps)

    for item in ir.get("liquid_classes") or []:
        if isinstance(item, dict):
            _normalize_field(item, "name", "liquid_class", maps)

    for dep in ir.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        kind = str(dep.get("kind") or "").lower()
        if kind == "liquid_class":
            _normalize_field(dep, "name", "liquid_class", maps)
        elif kind in {"device", "device_alias"}:
            _normalize_field(dep, "name", "device_alias", maps)
        elif kind in {"carrier", "rack_type"}:
            _normalize_field(dep, "name", "catalog", maps)

    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for key in ("target_labware", "source_labware", "destination_labware"):
            _normalize_field(step, key, "labware", maps)
        _normalize_field(step, "liquid_class", "liquid_class", maps)
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        for key in ("device_alias", "DeviceAlias"):
            _normalize_field(params, key, "device_alias", maps)
        for key in (
            "catalog",
            "Catalog",
            "labware_type",
            "LabwareType",
            "rack_type",
            "RackType",
            "forced_rack_type",
            "ForcedRackType",
            "carrier",
            "Carrier",
            "carrier_type",
            "CarrierType",
        ):
            _normalize_field(params, key, "catalog", maps)
        for key in (
            "label",
            "Label",
            "labware",
            "Labware",
            "labware_name",
            "LabwareName",
            "source_labware",
            "SourceLabware",
            "SourceLabwareName",
            "destination_labware",
            "DestinationLabware",
            "DestinationLabwareName",
        ):
            _normalize_field(params, key, "labware", maps)
        for key in ("LiquidClassName", "LiquidClassNameBySelection"):
            _normalize_field(params, key, "liquid_class", maps)

    return ir


def _normalize_field(
    item: dict[str, Any],
    key: str,
    kind: str,
    alias_maps: dict[str, dict[str, str]],
) -> None:
    if key in item and item[key] not in (None, ""):
        item[key] = resolve_alias(item[key], kind, alias_maps)


def _lookup_alias(
    value: str,
    keys: tuple[str, ...],
    alias_maps: dict[str, dict[str, str]],
) -> str | None:
    for key in keys:
        mapping = alias_maps.get(key, {})
        if value in mapping:
            return mapping[value]
        normalized = _norm(value)
        for alias, canonical in mapping.items():
            if _norm(alias) == normalized:
                return canonical
    return None


def _map_keys(kind: str) -> tuple[str, ...]:
    normalized = str(kind or "").strip().lower()
    return KIND_ALIASES.get(normalized, (normalized,))


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        val = str(raw_value or "").strip()
        if key and val:
            out[key] = val
    return out


def _load_yamlish(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return _load_simple_yaml(text)
    payload = yaml.safe_load(text) or {}
    return payload if isinstance(payload, dict) else {}


def _load_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        parts = _split_simple_yaml_key_value(line)
        if parts is None:
            continue
        key, raw_value = parts
        key = _parse_simple_yaml_key(key.strip())
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


def _split_simple_yaml_key_value(line: str) -> tuple[str, str] | None:
    quote: str | None = None
    escaped = False
    for idx, char in enumerate(line):
        if quote:
            if quote == '"' and char == "\\" and not escaped:
                escaped = True
                continue
            if char == quote and not escaped:
                quote = None
            escaped = False
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == ":":
            return line[:idx], line[idx + 1:]
    return None


def _parse_simple_yaml_key(raw_key: str) -> str:
    value = _parse_simple_yaml_scalar(raw_key)
    return str(value)


def _parse_simple_yaml_scalar(raw_value: str) -> Any:
    if raw_value == "null":
        return None
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    if raw_value.startswith(('"', "[", "{")):
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value.strip('"')
    if raw_value.startswith("'") and raw_value.endswith("'"):
        return raw_value[1:-1].replace("''", "'")
    try:
        return int(raw_value)
    except ValueError:
        pass
    try:
        return float(raw_value)
    except ValueError:
        return raw_value


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _strip_instance_suffix(value: str) -> str:
    return strip_fluent_instance_suffix(value)


def _allows_instance_strip(kind: str) -> bool:
    keys = set(_map_keys(kind))
    return bool(keys & {"labware_aliases", "catalog_aliases", "labware", "catalog"})
