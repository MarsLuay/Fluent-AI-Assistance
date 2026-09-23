"""Versioned, provider-neutral generation-context contract.

This module owns the small contract shared by later context selection work.
It deliberately extracts only explicit request/spec signals.  It does not
inspect or dump a ZEIA/XSCR archive and it never turns an unknown request into
an invented FluentControl capability.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


GENERATION_CONTEXT_SCHEMA_VERSION = "tecan.generation_context.v1"
SELECTION_MODES = ("explicit", "automatic_ranked", "source_required")
CONTEXT_STATUSES = ("ready", "needs_review")
ESTIMATED_TOKEN_CHARS = 4


class GenerationContextValidationError(ValueError):
    """Raised when a context payload cannot satisfy the versioned contract."""

    def __init__(self, errors: Sequence[Mapping[str, Any]]) -> None:
        self.errors = [dict(error) for error in errors]
        message = "; ".join(
            f"{error.get('path', '$')}: {error.get('code', 'invalid')}"
            for error in self.errors
        )
        super().__init__(message or "invalid generation context")


@dataclass(frozen=True)
class TaskFacets:
    """Deterministic, inspectable intent facets extracted from a request spec."""

    operation_families: tuple[str, ...] = ()
    labware_roles: tuple[str, ...] = ()
    labware_names: tuple[str, ...] = ()
    labware_types: tuple[str, ...] = ()
    device_families: tuple[str, ...] = ()
    liquid_roles: tuple[str, ...] = ()
    control_flow: tuple[str, ...] = ()
    worklist_requirements: tuple[str, ...] = ()
    external_process_requirements: tuple[str, ...] = ()
    barcode_attribute_variable_requirements: tuple[str, ...] = ()
    target_deck_regions: tuple[str, ...] = ()
    unknown_intent: tuple[str, ...] = ()
    unknown_fields: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_families": list(self.operation_families),
            "labware_roles": list(self.labware_roles),
            "labware_names": list(self.labware_names),
            "labware_types": list(self.labware_types),
            "device_families": list(self.device_families),
            "liquid_roles": list(self.liquid_roles),
            "control_flow": list(self.control_flow),
            "worklist_requirements": list(self.worklist_requirements),
            "external_process_requirements": list(self.external_process_requirements),
            "barcode_attribute_variable_requirements": list(self.barcode_attribute_variable_requirements),
            "target_deck_regions": list(self.target_deck_regions),
            "unknown_intent": list(self.unknown_intent),
            "unknown_fields": _canonical(self.unknown_fields),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskFacets":
        known = {
            "operation_families",
            "labware_roles",
            "labware_names",
            "labware_types",
            "device_families",
            "liquid_roles",
            "control_flow",
            "worklist_requirements",
            "external_process_requirements",
            "barcode_attribute_variable_requirements",
            "target_deck_regions",
            "unknown_intent",
            "unknown_fields",
        }
        unknown = dict(value.get("unknown_fields") or {})
        unknown.update({str(key): value[key] for key in value if key not in known})
        return cls(
            **{
                key: tuple(_stable_strings(value.get(key)))
                for key in known - {"unknown_fields"}
            },
            unknown_fields=_canonical(unknown),
        )


@dataclass(frozen=True)
class ContextEvidence:
    """One included, source/provenance-backed context item."""

    category: str
    content: Mapping[str, Any]
    selection_reason: str
    provenance: Mapping[str, Any]
    selection_mode: str = "source_required"
    item_id: str | None = None
    source_fingerprint: str | None = None
    conflict_key: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.category.strip():
            raise ValueError("evidence category is required")
        if not self.selection_reason.strip():
            raise ValueError("evidence selection_reason is required")
        if self.selection_mode not in SELECTION_MODES:
            raise ValueError(f"unsupported evidence selection_mode: {self.selection_mode!r}")
        if not self.provenance:
            raise ValueError("evidence provenance is required")
        source_fingerprint = self.source_fingerprint or _fingerprint(
            {"category": self.category, "content": self.content, "provenance": self.provenance}
        )
        item_id = self.item_id or f"{self.category}:{source_fingerprint[:16]}"
        object.__setattr__(self, "source_fingerprint", source_fingerprint)
        object.__setattr__(self, "item_id", item_id)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ContextEvidence":
        if not isinstance(value, Mapping):
            raise TypeError("evidence item must be a mapping")
        known = {
            "id",
            "item_id",
            "category",
            "content",
            "value",
            "selection_reason",
            "selection_mode",
            "provenance",
            "source_fingerprint",
            "conflict_key",
            "extensions",
        }
        extensions = dict(value.get("extensions") or {})
        extensions.update({str(key): value[key] for key in value if key not in known})
        content = value.get("content", value.get("value", {}))
        if not isinstance(content, Mapping):
            content = {"value": content}
        provenance = value.get("provenance")
        if not isinstance(provenance, Mapping):
            provenance = {} if provenance is None else {"source": str(provenance)}
        return cls(
            category=str(value.get("category") or ""),
            content=_canonical(content),
            selection_reason=str(value.get("selection_reason") or ""),
            provenance=_canonical(provenance),
            selection_mode=str(value.get("selection_mode") or "source_required"),
            item_id=str(value.get("item_id") or value.get("id") or "") or None,
            source_fingerprint=str(value.get("source_fingerprint") or "") or None,
            conflict_key=str(value.get("conflict_key") or "") or None,
            extensions=_canonical(extensions),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "category": self.category,
            "content": _canonical(self.content),
            "selection_reason": self.selection_reason,
            "selection_mode": self.selection_mode,
            "provenance": _canonical(self.provenance),
            "source_fingerprint": self.source_fingerprint,
            "conflict_key": self.conflict_key,
            "extensions": _canonical(self.extensions),
        }


@dataclass(frozen=True)
class ContextOmission:
    """A candidate intentionally left out of the included context."""

    candidate_id: str
    category: str
    reason: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("omission candidate_id is required")
        if not self.category.strip():
            raise ValueError("omission category is required")
        if not self.reason.strip():
            raise ValueError("omission reason is required")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ContextOmission":
        if not isinstance(value, Mapping):
            raise TypeError("omission must be a mapping")
        provenance = value.get("provenance")
        return cls(
            candidate_id=str(value.get("candidate_id") or value.get("id") or ""),
            category=str(value.get("category") or ""),
            reason=str(value.get("reason") or ""),
            provenance=_canonical(provenance) if isinstance(provenance, Mapping) else {},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "category": self.category,
            "reason": self.reason,
            "provenance": _canonical(self.provenance),
        }


def extract_task_facets(request_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Extract known request facets without guessing unsupported capabilities."""
    if not isinstance(request_spec, Mapping):
        raise TypeError("request_spec must be a mapping")

    values: dict[str, set[str]] = {name: set() for name in _FACET_NAMES}
    request = request_spec.get("request") if isinstance(request_spec.get("request"), Mapping) else request_spec
    source = request_spec.get("source") if isinstance(request_spec.get("source"), Mapping) else {}
    recipe = request_spec.get("verification_recipe")
    if not isinstance(recipe, Mapping):
        recipe = {}

    intent_text = " ".join(
        _stable_strings(
            [
                request.get("intent"),
                request.get("verbatim_prompt"),
                request.get("original_user_prompt"),
            ]
        )
    ).strip()
    _add_explicit_facets(values, request.get("task_facets"))
    _add_explicit_facets(values, request_spec.get("task_facets"))
    _extract_from_text(values, intent_text)
    _extract_structured_values(values, request_spec)
    _extract_structured_values(values, source)
    _extract_recipe(values, recipe)

    unknown_intent = set(_stable_strings(request.get("unknown_intent")))
    if intent_text and not values["operation_families"] and not _has_known_signal(values):
        unknown_intent.add(intent_text)
    unknown_fields = _unknown_facet_fields(request.get("task_facets"))
    unknown_fields.update(_unknown_facet_fields(request_spec.get("task_facets")))
    return TaskFacets(
        **{name: tuple(sorted(values[name], key=str.casefold)) for name in _FACET_NAMES},
        unknown_intent=tuple(sorted(unknown_intent, key=str.casefold)),
        unknown_fields=_canonical(unknown_fields),
    ).as_dict()


def build_generation_context(
    request_spec: Mapping[str, Any],
    *,
    evidence_items: Iterable[Mapping[str, Any] | ContextEvidence] = (),
    omissions: Iterable[Mapping[str, Any] | ContextOmission] = (),
    target_provenance: Mapping[str, Any] | None = None,
    status: str | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable v1 context payload from a validated request."""
    facets = TaskFacets.from_mapping(extract_task_facets(request_spec))
    try:
        evidence = tuple(
            item if isinstance(item, ContextEvidence) else ContextEvidence.from_mapping(item)
            for item in evidence_items
        )
        omitted = tuple(
            item if isinstance(item, ContextOmission) else ContextOmission.from_mapping(item)
            for item in omissions
        )
    except (TypeError, ValueError) as exc:
        raise GenerationContextValidationError(
            [{"path": "$.evidence_items", "code": "invalid_context_item", "message": str(exc)}]
        ) from exc
    payload: dict[str, Any] = {
        "schema_version": GENERATION_CONTEXT_SCHEMA_VERSION,
        "request_fingerprint": _fingerprint(_request_fingerprint_material(request_spec)),
        "target_provenance": _canonical(target_provenance or {}),
        "task_facets": facets.as_dict(),
        "evidence_items": [item.as_dict() for item in evidence],
        "omissions": [item.as_dict() for item in omitted],
        "status": status or ("needs_review" if facets.unknown_intent else "ready"),
        "extensions": _canonical(extensions or {}),
    }
    payload["accounting"] = _accounting(payload)
    payload["context_fingerprint"] = _fingerprint(payload)
    errors = validate_generation_context(payload)
    if errors:
        raise GenerationContextValidationError(errors)
    return payload


def normalize_generation_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a v1 payload while preserving forward-compatible fields."""
    if not isinstance(payload, Mapping):
        raise GenerationContextValidationError([{"path": "$", "code": "payload_must_be_mapping"}])
    known = {
        "schema_version",
        "request_fingerprint",
        "target_provenance",
        "task_facets",
        "evidence_items",
        "omissions",
        "status",
        "extensions",
        "accounting",
        "context_fingerprint",
    }
    extensions = dict(payload.get("extensions") or {})
    unknown = dict(extensions.get("unknown_fields") or {})
    unknown.update({str(key): _canonical(value) for key, value in payload.items() if key not in known})
    if unknown:
        extensions["unknown_fields"] = unknown
    normalized = {
        key: _canonical(payload[key])
        for key in known
        if key in payload and key not in {"accounting", "context_fingerprint", "extensions"}
    }
    normalized["extensions"] = _canonical(extensions)
    normalized["accounting"] = _accounting(normalized)
    normalized["context_fingerprint"] = _fingerprint(normalized)
    errors = validate_generation_context(normalized)
    if errors:
        raise GenerationContextValidationError(errors)
    return normalized


def generation_context_json_schema() -> dict[str, Any]:
    """Return the machine-readable v1 contract without a provider dependency."""
    facet_properties = {
        name: {"type": "array", "items": {"type": "string"}}
        for name in _FACET_NAMES + ("unknown_intent",)
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://fluent-ai-assistance.local/schemas/generation_context.v1.schema.json",
        "title": "Tecan generation context",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "request_fingerprint",
            "target_provenance",
            "task_facets",
            "evidence_items",
            "omissions",
            "status",
            "extensions",
            "accounting",
            "context_fingerprint",
        ],
        "properties": {
            "schema_version": {"const": GENERATION_CONTEXT_SCHEMA_VERSION},
            "request_fingerprint": {"type": "string", "minLength": 1},
            "target_provenance": {"type": "object"},
            "task_facets": {
                "type": "object",
                "required": [*facet_properties, "unknown_fields"],
                "properties": {
                    **facet_properties,
                    "unknown_fields": {"type": "object"},
                },
                "additionalProperties": False,
            },
            "evidence_items": {"type": "array", "items": {"$ref": "#/$defs/evidence"}},
            "omissions": {"type": "array", "items": {"$ref": "#/$defs/omission"}},
            "status": {"enum": list(CONTEXT_STATUSES)},
            "extensions": {"type": "object"},
            "accounting": {"$ref": "#/$defs/accounting"},
            "context_fingerprint": {"type": "string", "minLength": 1},
        },
        "$defs": {
            "evidence": {
                "type": "object",
                "required": ["id", "category", "content", "selection_reason", "selection_mode", "provenance", "source_fingerprint"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "category": {"type": "string", "minLength": 1},
                    "content": {"type": "object"},
                    "selection_reason": {"type": "string", "minLength": 1},
                    "selection_mode": {"enum": list(SELECTION_MODES)},
                    "provenance": {"type": "object", "minProperties": 1},
                    "source_fingerprint": {"type": "string", "minLength": 1},
                    "conflict_key": {"type": ["string", "null"]},
                    "extensions": {"type": "object"},
                },
                "additionalProperties": False,
            },
            "omission": {
                "type": "object",
                "required": ["candidate_id", "category", "reason", "provenance"],
                "properties": {
                    "candidate_id": {"type": "string", "minLength": 1},
                    "category": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                    "provenance": {"type": "object"},
                },
                "additionalProperties": False,
            },
            "accounting": {
                "type": "object",
                "required": [
                    "evidence_item_count",
                    "omission_count",
                    "categories",
                    "source_fingerprint_count",
                    "serialized_characters",
                    "serialized_bytes",
                    "estimated_tokens",
                ],
                "properties": {
                    "evidence_item_count": {"type": "integer", "minimum": 0},
                    "omission_count": {"type": "integer", "minimum": 0},
                    "categories": {"type": "object"},
                    "source_fingerprint_count": {"type": "integer", "minimum": 0},
                    "serialized_characters": {"type": "integer", "minimum": 0},
                    "serialized_bytes": {"type": "integer", "minimum": 0},
                    "estimated_tokens": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        },
    }


def validate_generation_context(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return contract errors; unknown extension fields are preserved, not rejected."""
    errors: list[dict[str, Any]] = []
    if not isinstance(payload, Mapping):
        return [{"path": "$", "code": "payload_must_be_mapping"}]
    required = (
        "schema_version",
        "request_fingerprint",
        "task_facets",
        "evidence_items",
        "omissions",
        "status",
        "accounting",
        "context_fingerprint",
    )
    for key in required:
        if key not in payload:
            errors.append({"path": f"$.{key}", "code": "missing_required_field"})
    if payload.get("schema_version") != GENERATION_CONTEXT_SCHEMA_VERSION:
        errors.append({"path": "$.schema_version", "code": "unsupported_schema_version"})
    if payload.get("status") not in CONTEXT_STATUSES:
        errors.append({"path": "$.status", "code": "unsupported_status"})

    facets = payload.get("task_facets")
    if not isinstance(facets, Mapping):
        errors.append({"path": "$.task_facets", "code": "must_be_mapping"})
    elif not isinstance(facets.get("unknown_fields", {}), Mapping):
        errors.append({"path": "$.task_facets.unknown_fields", "code": "must_be_mapping"})

    items = payload.get("evidence_items")
    seen_ids: set[str] = set()
    seen_conflicts: dict[tuple[str, str], str] = {}
    if not isinstance(items, list):
        errors.append({"path": "$.evidence_items", "code": "must_be_list"})
    else:
        for index, raw in enumerate(items):
            path = f"$.evidence_items[{index}]"
            if not isinstance(raw, Mapping):
                errors.append({"path": path, "code": "must_be_mapping"})
                continue
            item_id = str(raw.get("id") or raw.get("item_id") or "")
            category = str(raw.get("category") or "")
            if not item_id:
                errors.append({"path": f"{path}.id", "code": "missing_required_field"})
            elif item_id in seen_ids:
                errors.append({"path": f"{path}.id", "code": "duplicate_evidence_id", "id": item_id})
            seen_ids.add(item_id)
            if not str(raw.get("selection_reason") or "").strip():
                errors.append({"path": f"{path}.selection_reason", "code": "missing_required_field"})
            if not isinstance(raw.get("provenance"), Mapping) or not raw.get("provenance"):
                errors.append({"path": f"{path}.provenance", "code": "missing_required_field"})
            if not str(raw.get("source_fingerprint") or "").strip():
                errors.append({"path": f"{path}.source_fingerprint", "code": "missing_required_field"})
            if raw.get("selection_mode") not in SELECTION_MODES:
                errors.append({"path": f"{path}.selection_mode", "code": "unsupported_selection_mode"})
            conflict_key = str(raw.get("conflict_key") or "")
            if conflict_key:
                key = (category, conflict_key)
                content_hash = _fingerprint(raw.get("content", raw.get("value", {})))
                previous = seen_conflicts.get(key)
                if previous is not None and previous != content_hash:
                    errors.append({"path": path, "code": "conflicting_evidence", "conflict_key": conflict_key})
                seen_conflicts[key] = content_hash

    omitted = payload.get("omissions")
    if not isinstance(omitted, list):
        errors.append({"path": "$.omissions", "code": "must_be_list"})
    else:
        for index, raw in enumerate(omitted):
            path = f"$.omissions[{index}]"
            if not isinstance(raw, Mapping):
                errors.append({"path": path, "code": "must_be_mapping"})
                continue
            for field_name in ("candidate_id", "category", "reason"):
                if not str(raw.get(field_name) or "").strip():
                    errors.append({"path": f"{path}.{field_name}", "code": "missing_required_field"})

    if isinstance(payload.get("accounting"), Mapping):
        expected = _accounting(payload)
        if _canonical(payload["accounting"]) != _canonical(expected):
            errors.append({"path": "$.accounting", "code": "accounting_mismatch"})
    if payload.get("context_fingerprint"):
        fingerprint_material = dict(payload)
        fingerprint_material.pop("context_fingerprint", None)
        expected_fingerprint = _fingerprint(fingerprint_material)
        if payload.get("context_fingerprint") != expected_fingerprint:
            errors.append({"path": "$.context_fingerprint", "code": "fingerprint_mismatch"})
    return errors


def assert_valid_generation_context(payload: Mapping[str, Any]) -> None:
    errors = validate_generation_context(payload)
    if errors:
        raise GenerationContextValidationError(errors)


def _accounting(payload: Mapping[str, Any]) -> dict[str, Any]:
    material = dict(payload)
    material.pop("accounting", None)
    material.pop("context_fingerprint", None)
    serialized = json.dumps(_canonical(material), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    items = payload.get("evidence_items") if isinstance(payload.get("evidence_items"), list) else []
    omissions = payload.get("omissions") if isinstance(payload.get("omissions"), list) else []
    categories: dict[str, int] = {}
    for item in items:
        if isinstance(item, Mapping):
            category = str(item.get("category") or "unknown")
            categories[category] = categories.get(category, 0) + 1
    return {
        "evidence_item_count": len(items),
        "omission_count": len(omissions),
        "categories": dict(sorted(categories.items(), key=lambda row: row[0].casefold())),
        "source_fingerprint_count": len(
            {str(item.get("source_fingerprint")) for item in items if isinstance(item, Mapping) and item.get("source_fingerprint")}
        ),
        "serialized_characters": len(serialized),
        "serialized_bytes": len(serialized.encode("utf-8")),
        "estimated_tokens": (len(serialized) + ESTIMATED_TOKEN_CHARS - 1) // ESTIMATED_TOKEN_CHARS,
    }


_FACET_NAMES = (
    "operation_families",
    "labware_roles",
    "labware_names",
    "labware_types",
    "device_families",
    "liquid_roles",
    "control_flow",
    "worklist_requirements",
    "external_process_requirements",
    "barcode_attribute_variable_requirements",
    "target_deck_regions",
)
_TEXT_OPERATION_PATTERNS = {
    "aspirate": (r"\baspirat(?:e|ing|ion)\b", r"\bpipett(?:e|ing)\b"),
    "dispense": (r"\bdispens(?:e|ing)\b",),
    "mix": (r"\bmix(?:ing)?\b",),
    "move_labware": (r"\bmove\b.{0,24}\b(?:plate|labware|container)\b", r"\btransfer\b.{0,24}\b(?:plate|labware|container)\b"),
    "wash": (r"\bwash(?:ing)?\b",),
    "worklist": (r"\bworklist\b", r"\b(?:load|execute)[ -]?worklist\b",),
    "driver_command": (r"\bdriver\b", r"\bmacro\b"),
    "external_process": (r"\bexternal process\b", r"\bvb script\b", r"\bpython\b"),
    "prompt": (r"\bprompt\b", r"\bquery\b", r"\boperator question\b"),
    "subroutine": (r"\bsubroutine\b", r"\bjoin\b"),
}
_DEVICE_TOKENS = ("liha", "mca96", "mca384", "rga", "cga", "bcr", "robot", "fluentcontrol")
_CONTROL_TOKENS = ("loop", "conditional", "condition", "if", "while", "repeat", "prompt", "query", "subroutine")


def _extract_from_text(values: dict[str, set[str]], text: str) -> None:
    lowered = text.casefold()
    for facet, patterns in _TEXT_OPERATION_PATTERNS.items():
        if any(re.search(pattern, lowered) for pattern in patterns):
            values["operation_families"].add(facet)
    for token in _DEVICE_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            values["device_families"].add(token.upper() if token != "fluentcontrol" else "FluentControl")
    for token in _CONTROL_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            values["control_flow"].add(token)
    if re.search(r"\b(?:barcode|scan)\b", lowered):
        values["barcode_attribute_variable_requirements"].add("barcode")
    if re.search(r"\battribute\b", lowered):
        values["barcode_attribute_variable_requirements"].add("attribute")
    if re.search(r"\bvariable\b", lowered):
        values["barcode_attribute_variable_requirements"].add("variable")


def _extract_structured_values(values: dict[str, set[str]], value: Any) -> None:
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            key = str(raw_key).casefold().replace("-", "_")
            strings = _stable_strings(raw_value)
            if key in {"operation", "operations", "operation_family", "operation_families", "pattern_type"}:
                values["operation_families"].update(
                    normalized
                    for item in strings
                    if (normalized := _normalize_operation(item)) in _KNOWN_OPERATION_FAMILIES
                )
            elif key in {"labware_role", "labware_roles", "role"}:
                values["labware_roles"].update(strings)
            elif key in {"labware", "labware_name", "target_labware", "source_labware", "destination_labware", "label"}:
                values["labware_names"].update(strings)
                if key == "source_labware":
                    values["labware_roles"].add("source")
                elif key == "destination_labware":
                    values["labware_roles"].add("destination")
                elif key == "target_labware":
                    values["labware_roles"].add("target")
            elif key in {"catalog", "labware_type", "catalog_name", "labware_types"}:
                values["labware_types"].update(strings)
            elif key in {"device", "device_alias", "device_family", "head", "pipetting_head", "arm", "module_name"}:
                values["device_families"].update(strings)
            elif key in {"liquid", "liquid_class", "liquid_classes", "reagent", "reagents", "liquid_role", "liquid_roles"}:
                values["liquid_roles"].update(strings)
            elif key in {"site", "site_name", "destination_site", "location", "deck_region", "target_deck_region"}:
                values["target_deck_regions"].update(strings)
            elif key in {"worklist", "worklist_path", "worklist_requirements"}:
                values["worklist_requirements"].update(strings or {"worklist"})
            elif key in {"driver_macro", "driver_command", "application_driver_macro", "legacy_driver_macro"}:
                values["external_process_requirements"].update(strings or {"driver_command"})
            elif key in {"subroutine", "subroutines"}:
                values["control_flow"].add("subroutine")
            elif key in {"barcode", "barcode_requirements"}:
                values["barcode_attribute_variable_requirements"].add("barcode")
            elif key in {"attribute", "attributes"}:
                values["barcode_attribute_variable_requirements"].add("attribute")
            elif key in {"variable", "variables", "declared_variables"}:
                values["barcode_attribute_variable_requirements"].add("variable")
            _extract_structured_values(values, raw_value)
    elif isinstance(value, list):
        for item in value:
            _extract_structured_values(values, item)


def _extract_recipe(values: dict[str, set[str]], recipe: Mapping[str, Any]) -> None:
    steps = recipe.get("steps") if isinstance(recipe.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        step_type = str(step.get("type") or next((key for key in _CONTROL_STEP_KEYS if key in step), "")).casefold()
        if step_type:
            normalized = _normalize_operation(step_type)
            if normalized in {"move_labware", "aspirate", "dispense", "mix", "wash", "worklist", "driver_command", "external_process", "prompt"}:
                values["operation_families"].add(normalized)
            if step_type in {"loop", "while", "conditional", "if", "repeat", "subroutine", "query_variable", "prompt"}:
                values["control_flow"].add(step_type)
        _extract_structured_values(values, step)


_CONTROL_STEP_KEYS = ("loop", "while", "conditional", "if", "repeat", "subroutine", "query_variable", "prompt")


def _add_explicit_facets(values: dict[str, set[str]], raw: Any) -> None:
    if not isinstance(raw, Mapping):
        return
    for name in _FACET_NAMES:
        if name in raw:
            values[name].update(_stable_strings(raw.get(name)))


def _unknown_facet_fields(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    known = set(_FACET_NAMES) | {"unknown_intent", "unknown_fields"}
    return {str(key): _canonical(value) for key, value in raw.items() if key not in known}


def _has_known_signal(values: Mapping[str, set[str]]) -> bool:
    return any(values[name] for name in _FACET_NAMES)


def _normalize_operation(value: str) -> str:
    normalized = value.casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "move_plate": "move_labware",
        "transfer_labware": "move_labware",
        "pipette": "aspirate",
        "driver_macro": "driver_command",
        "application_driver_macro": "driver_command",
        "legacy_driver_macro": "driver_command",
        "execute_vb_script": "external_process",
        "execute_application": "external_process",
        "query_variable": "prompt",
    }
    return aliases.get(normalized, normalized)


_KNOWN_OPERATION_FAMILIES = {
    "aspirate",
    "dispense",
    "mix",
    "move_labware",
    "wash",
    "worklist",
    "driver_command",
    "external_process",
    "prompt",
    "subroutine",
}


def _request_fingerprint_material(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _request_fingerprint_material(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).casefold() not in {"created_at", "updated_at", "generated_at", "timestamp"}
        }
    if isinstance(value, list):
        return [_request_fingerprint_material(item) for item in value]
    if isinstance(value, tuple):
        return [_request_fingerprint_material(item) for item in value]
    return value


def _stable_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return []
    if isinstance(value, (list, tuple, set)):
        values = [
            str(item).strip()
            for item in value
            if item is not None and not isinstance(item, Mapping) and str(item).strip()
        ]
    else:
        values = [str(value).strip()] if str(value).strip() else []
    return sorted(set(values), key=str.casefold)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, set):
        return sorted((_canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _fingerprint(value: Any) -> str:
    serialized = json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "CONTEXT_STATUSES",
    "ContextEvidence",
    "ContextOmission",
    "GENERATION_CONTEXT_SCHEMA_VERSION",
    "GenerationContextValidationError",
    "SELECTION_MODES",
    "TaskFacets",
    "assert_valid_generation_context",
    "build_generation_context",
    "extract_task_facets",
    "generation_context_json_schema",
    "normalize_generation_context",
    "validate_generation_context",
]
