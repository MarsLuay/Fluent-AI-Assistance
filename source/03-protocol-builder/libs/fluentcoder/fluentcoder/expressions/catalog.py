"""Versioned, provenance-backed FluentControl expression symbol catalog."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
import re
from typing import Any, Mapping


CATALOG_SCHEMA = "tecan.expression_symbols.v1"
CATALOG_SCHEMA_VERSION = 1
_DEFAULT_RESOURCE = ("_assets", "reference", "expression_symbols.json")
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
_SYMBOL_KINDS = {"function", "mathematical_function", "runtime_function", "constant"}
_EXPRESSION_TYPES = {"any", "boolean", "number", "string", "unknown"}


class CatalogValidationError(ValueError):
    """Raised when a symbol catalog is malformed or ambiguous."""


@dataclass(frozen=True)
class CatalogVersionRange:
    minimum: str | None = None
    maximum: str | None = None

    def contains(self, version: str) -> bool:
        candidate = _parse_version(version)
        if self.minimum is not None and candidate < _parse_version(self.minimum):
            return False
        if self.maximum is not None and candidate > _parse_version(self.maximum):
            return False
        return True


@dataclass(frozen=True)
class CatalogProvenance:
    source: str
    reference: str
    note: str = ""


@dataclass(frozen=True)
class CatalogSignature:
    argument_types: tuple[str, ...] = ()
    return_type: str = "unknown"
    variadic_type: str | None = None
    min_arguments: int | None = None
    max_arguments: int | None = None

    def to_function_signature(self, name: str) -> Any:
        # Import lazily so semantics.py can use this module for its defaults.
        from .semantics import FunctionSignature

        return FunctionSignature(
            name=name,
            argument_types=self.argument_types,
            return_type=self.return_type,
            variadic_type=self.variadic_type,
            min_arguments=self.min_arguments,
            max_arguments=self.max_arguments,
        )


@dataclass(frozen=True)
class ExpressionSymbol:
    name: str
    kind: str
    signatures: tuple[CatalogSignature, ...]
    contexts: tuple[str, ...]
    version_ranges: tuple[CatalogVersionRange, ...]
    provenance: tuple[CatalogProvenance, ...]
    confidence: float
    description: str = ""

    @property
    def normalized_name(self) -> str:
        return self.name.casefold()

    def version_status(self, target_version: str | None) -> str:
        if not self.version_ranges:
            return "supported"
        if not target_version:
            return "cannot_determine"
        return "supported" if any(version_range.contains(target_version) for version_range in self.version_ranges) else "unsupported"


@dataclass(frozen=True)
class ExpressionSymbolCatalog:
    schema: str
    schema_version: int
    symbols: tuple[ExpressionSymbol, ...]

    def lookup(self, name: str) -> ExpressionSymbol | None:
        normalized = str(name or "").strip().casefold()
        return next((symbol for symbol in self.symbols if symbol.normalized_name == normalized), None)

    def version_status(self, name: str, target_version: str | None) -> str:
        symbol = self.lookup(name)
        return "unknown_symbol" if symbol is None else symbol.version_status(target_version)

    def function_signatures(self, target_version: str | None = None) -> dict[str, Any]:
        """Return the compatibility view consumed by expression semantics.

        Overloads remain available on ``ExpressionSymbol.signatures`` for the
        catalog consumers that understand them. The current semantic layer has
        one signature per normalized name, so it uses the first catalog entry
        while preserving every overload in the source catalog.
        """

        signatures: dict[str, Any] = {}
        for symbol in self.symbols:
            if symbol.kind not in {"function", "mathematical_function", "runtime_function"}:
                continue
            if symbol.version_status(target_version) == "unsupported":
                continue
            if symbol.signatures:
                signatures[symbol.normalized_name] = symbol.signatures[0].to_function_signature(symbol.name)
        return signatures

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schemaVersion": self.schema_version,
            "symbols": [_symbol_to_mapping(symbol) for symbol in self.symbols],
        }


def load_expression_symbol_catalog(source: Mapping[str, Any] | str | Path | None = None) -> ExpressionSymbolCatalog:
    """Load and validate a catalog from a mapping, JSON path, or package asset."""

    payload = _read_payload(source)
    return _catalog_from_mapping(payload)


def _read_payload(source: Mapping[str, Any] | str | Path | None) -> Mapping[str, Any]:
    if source is None:
        resource = files("fluentcoder")
        for part in _DEFAULT_RESOURCE:
            resource = resource.joinpath(part)
        return json.loads(resource.read_text(encoding="utf-8"))
    if isinstance(source, Mapping):
        return source
    path = Path(source)
    return json.loads(path.read_text(encoding="utf-8"))


def _catalog_from_mapping(payload: Mapping[str, Any]) -> ExpressionSymbolCatalog:
    if not isinstance(payload, Mapping):
        raise CatalogValidationError("catalog must be a JSON object")
    if payload.get("schema") != CATALOG_SCHEMA:
        raise CatalogValidationError(f"schema must be {CATALOG_SCHEMA!r}")
    if payload.get("schemaVersion") != CATALOG_SCHEMA_VERSION:
        raise CatalogValidationError(f"schemaVersion must be {CATALOG_SCHEMA_VERSION}")
    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list):
        raise CatalogValidationError("symbols must be a list")

    symbols: list[ExpressionSymbol] = []
    seen: set[str] = set()
    for index, raw_symbol in enumerate(raw_symbols):
        symbol = _parse_symbol(raw_symbol, index)
        if symbol.normalized_name in seen:
            raise CatalogValidationError(f"duplicate_symbol: {symbol.name!r}")
        seen.add(symbol.normalized_name)
        symbols.append(symbol)
    symbols.sort(key=lambda symbol: symbol.normalized_name)
    return ExpressionSymbolCatalog(CATALOG_SCHEMA, CATALOG_SCHEMA_VERSION, tuple(symbols))


def _parse_symbol(raw_symbol: Any, index: int) -> ExpressionSymbol:
    if not isinstance(raw_symbol, Mapping):
        raise CatalogValidationError(f"symbols[{index}] must be an object")
    name = _required_text(raw_symbol.get("name"), f"symbols[{index}].name")
    kind = _required_text(raw_symbol.get("kind"), f"symbols[{index}].kind")
    if kind not in _SYMBOL_KINDS:
        raise CatalogValidationError(f"symbols[{index}].kind is unsupported: {kind!r}")
    contexts = _string_tuple(raw_symbol.get("contexts", []), f"symbols[{index}].contexts")
    provenance = _parse_provenance(raw_symbol.get("provenance"), index)
    confidence = raw_symbol.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise CatalogValidationError(f"symbols[{index}].confidence must be a number from 0 to 1")
    version_ranges = _parse_version_ranges(raw_symbol.get("versionRanges", []), index)
    raw_signatures = raw_symbol.get("signatures", [])
    if not isinstance(raw_signatures, list):
        raise CatalogValidationError(f"symbols[{index}].signatures must be a list")
    if kind in {"function", "mathematical_function", "runtime_function"} and not raw_signatures:
        raise CatalogValidationError(f"symbols[{index}] function symbols require a signature")
    signatures = tuple(_parse_signature(value, index, signature_index) for signature_index, value in enumerate(raw_signatures))
    signature_keys = {_signature_key(signature) for signature in signatures}
    if len(signature_keys) != len(signatures):
        raise CatalogValidationError(f"symbols[{index}] contains duplicate signatures")
    return ExpressionSymbol(
        name=name,
        kind=kind,
        signatures=signatures,
        contexts=contexts,
        version_ranges=version_ranges,
        provenance=provenance,
        confidence=float(confidence),
        description=str(raw_symbol.get("description") or ""),
    )


def _parse_signature(raw_signature: Any, symbol_index: int, signature_index: int) -> CatalogSignature:
    prefix = f"symbols[{symbol_index}].signatures[{signature_index}]"
    if not isinstance(raw_signature, Mapping):
        raise CatalogValidationError(f"{prefix} must be an object")
    argument_types = _string_tuple(raw_signature.get("argumentTypes", []), f"{prefix}.argumentTypes")
    for type_name in argument_types:
        if type_name not in _EXPRESSION_TYPES:
            raise CatalogValidationError(f"{prefix}.argumentTypes contains unsupported type {type_name!r}")
    return_type = str(raw_signature.get("returnType") or "unknown")
    if return_type not in _EXPRESSION_TYPES:
        raise CatalogValidationError(f"{prefix}.returnType contains unsupported type {return_type!r}")
    variadic_type = raw_signature.get("variadicType")
    if variadic_type is not None:
        variadic_type = _required_text(variadic_type, f"{prefix}.variadicType")
        if variadic_type not in _EXPRESSION_TYPES:
            raise CatalogValidationError(f"{prefix}.variadicType contains unsupported type {variadic_type!r}")
    minimum = _optional_non_negative_int(raw_signature.get("minArguments"), f"{prefix}.minArguments")
    maximum = _optional_non_negative_int(raw_signature.get("maxArguments"), f"{prefix}.maxArguments")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise CatalogValidationError(f"{prefix} minArguments cannot exceed maxArguments")
    return CatalogSignature(tuple(argument_types), return_type, variadic_type, minimum, maximum)


def _parse_provenance(raw_provenance: Any, symbol_index: int) -> tuple[CatalogProvenance, ...]:
    if not isinstance(raw_provenance, list) or not raw_provenance:
        raise CatalogValidationError(f"symbols[{symbol_index}].provenance must be a non-empty list")
    records: list[CatalogProvenance] = []
    for provenance_index, value in enumerate(raw_provenance):
        prefix = f"symbols[{symbol_index}].provenance[{provenance_index}]"
        if not isinstance(value, Mapping):
            raise CatalogValidationError(f"{prefix} must be an object")
        records.append(
            CatalogProvenance(
                source=_required_text(value.get("source"), f"{prefix}.source"),
                reference=_required_text(value.get("reference"), f"{prefix}.reference"),
                note=str(value.get("note") or ""),
            )
        )
    return tuple(records)


def _parse_version_ranges(raw_ranges: Any, symbol_index: int) -> tuple[CatalogVersionRange, ...]:
    if not isinstance(raw_ranges, list):
        raise CatalogValidationError(f"symbols[{symbol_index}].versionRanges must be a list")
    ranges: list[CatalogVersionRange] = []
    for range_index, value in enumerate(raw_ranges):
        prefix = f"symbols[{symbol_index}].versionRanges[{range_index}]"
        if not isinstance(value, Mapping):
            raise CatalogValidationError(f"{prefix} must be an object")
        minimum = _optional_version(value.get("min"), f"{prefix}.min")
        maximum = _optional_version(value.get("max"), f"{prefix}.max")
        if minimum is None and maximum is None:
            raise CatalogValidationError(f"{prefix} must specify min or max")
        if minimum is not None and maximum is not None and _parse_version(minimum) > _parse_version(maximum):
            raise CatalogValidationError(f"{prefix} min cannot exceed max")
        ranges.append(CatalogVersionRange(minimum, maximum))
    return tuple(ranges)


def _symbol_to_mapping(symbol: ExpressionSymbol) -> dict[str, Any]:
    return {
        "name": symbol.name,
        "kind": symbol.kind,
        "contexts": list(symbol.contexts),
        "versionRanges": [{key: value for key, value in (("min", item.minimum), ("max", item.maximum)) if value is not None} for item in symbol.version_ranges],
        "provenance": [
            {key: value for key, value in (("source", item.source), ("reference", item.reference), ("note", item.note)) if value}
            for item in symbol.provenance
        ],
        "confidence": symbol.confidence,
        "signatures": [
            {
                key: value
                for key, value in (
                    ("argumentTypes", list(signature.argument_types)),
                    ("returnType", signature.return_type),
                    ("variadicType", signature.variadic_type),
                    ("minArguments", signature.min_arguments),
                    ("maxArguments", signature.max_arguments),
                )
                if value is not None
            }
            for signature in symbol.signatures
        ],
        **({"description": symbol.description} if symbol.description else {}),
    }


def _signature_key(signature: CatalogSignature) -> tuple[Any, ...]:
    return (signature.argument_types, signature.return_type, signature.variadic_type, signature.min_arguments, signature.max_arguments)


def _required_text(value: Any, path: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CatalogValidationError(f"{path} must be non-empty")
    return text


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise CatalogValidationError(f"{path} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _optional_non_negative_int(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogValidationError(f"{path} must be a non-negative integer")
    return value


def _optional_version(value: Any, path: str) -> str | None:
    if value is None:
        return None
    version = _required_text(value, path)
    _parse_version(version, path)
    return version


def _parse_version(value: str, path: str = "version") -> tuple[int, ...]:
    if not _VERSION_RE.fullmatch(value):
        raise CatalogValidationError(f"{path} must be a dotted numeric version")
    parts = tuple(int(part) for part in value.split("."))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts
