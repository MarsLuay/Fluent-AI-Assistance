from __future__ import annotations

import pytest

from fluentcoder.expressions import (
    CatalogValidationError,
    check_expression_semantics,
    load_expression_symbol_catalog,
    parse_expression,
)


def _symbol(
    name: str,
    *,
    signatures: list[dict] | None = None,
    version_ranges: list[dict] | None = None,
    provenance: list[dict] | None = None,
) -> dict:
    return {
        "name": name,
        "kind": "function",
        "contexts": ["script_expression"],
        "versionRanges": version_ranges or [],
        "provenance": provenance if provenance is not None else [{"source": "fixture", "reference": "catalog-fixture"}],
        "confidence": 0.9,
        "signatures": signatures or [{"argumentTypes": ["string"], "returnType": "string"}],
    }


def _catalog(*symbols: dict) -> dict:
    return {"schema": "tecan.expression_symbols.v1", "schemaVersion": 1, "symbols": list(symbols)}


def test_packaged_catalog_is_deterministic_and_preserves_canonical_spelling() -> None:
    catalog = load_expression_symbol_catalog()

    assert catalog.schema == "tecan.expression_symbols.v1"
    assert [symbol.name.casefold() for symbol in catalog.symbols] == sorted(symbol.name.casefold() for symbol in catalog.symbols)
    assert catalog.lookup("gEtAtTrIbUtE").name == "GetAttribute"
    assert catalog.lookup("GetAttribute").provenance[0].source == "repository_behavior"

    signatures = catalog.function_signatures()
    assert signatures["getattribute"].name == "GetAttribute"
    assert signatures["round"].min_count == 1
    assert signatures["round"].max_count == 2
    assert check_expression_semantics(parse_expression("Round(2.1, 0)" )).valid


def test_catalog_preserves_overloads_and_serializes_in_stable_order() -> None:
    catalog = load_expression_symbol_catalog(
        _catalog(
            _symbol(
                "Overloaded",
                signatures=[
                    {"argumentTypes": ["string"], "returnType": "string"},
                    {"argumentTypes": ["number"], "returnType": "number"},
                ],
            ),
            _symbol("Other"),
        )
    )

    overloaded = catalog.lookup("overloaded")
    assert len(overloaded.signatures) == 2
    assert [symbol.name for symbol in catalog.symbols] == ["Other", "Overloaded"]
    assert catalog.as_mapping()["symbols"][1]["name"] == "Overloaded"


def test_catalog_rejects_duplicate_names_and_signatures_case_insensitively() -> None:
    with pytest.raises(CatalogValidationError, match="duplicate_symbol"):
        load_expression_symbol_catalog(_catalog(_symbol("Fn"), _symbol("fn")))

    duplicate_signature = _symbol(
        "Fn",
        signatures=[
            {"argumentTypes": ["string"], "returnType": "string"},
            {"argumentTypes": ["string"], "returnType": "string"},
        ],
    )
    with pytest.raises(CatalogValidationError, match="duplicate signatures"):
        load_expression_symbol_catalog(_catalog(duplicate_signature))


def test_catalog_requires_provenance_and_valid_signature_types() -> None:
    missing_provenance = _symbol("Fn", provenance=[])
    with pytest.raises(CatalogValidationError, match="provenance"):
        load_expression_symbol_catalog(_catalog(missing_provenance))

    invalid_type = _symbol("Fn", signatures=[{"argumentTypes": ["vendor_object"], "returnType": "string"}])
    with pytest.raises(CatalogValidationError, match="unsupported type"):
        load_expression_symbol_catalog(_catalog(invalid_type))


def test_catalog_version_ranges_are_explicit_and_fail_closed_when_unknown() -> None:
    catalog = load_expression_symbol_catalog(
        _catalog(
            _symbol("Versioned", version_ranges=[{"min": "3.2", "max": "3.8"}]),
            _symbol("Unversioned"),
        )
    )

    assert catalog.version_status("Versioned", "3.5") == "supported"
    assert catalog.version_status("Versioned", "3.8.0") == "supported"
    assert catalog.version_status("Versioned", "4.0") == "unsupported"
    assert catalog.version_status("Versioned", None) == "cannot_determine"
    assert catalog.version_status("Unversioned", None) == "supported"
    assert catalog.version_status("Missing", "3.5") == "unknown_symbol"


def test_catalog_introspection_and_generation_policy_are_version_aware() -> None:
    catalog = load_expression_symbol_catalog(
        _catalog(
            _symbol(
                "Versioned",
                signatures=[
                    {"argumentTypes": ["number"], "returnType": "number"},
                    {"argumentTypes": ["string"], "returnType": "string"},
                ],
                version_ranges=[{"min": "3.2", "max": "3.8"}],
                provenance=[{"source": "verified", "reference": "fixture.pdf"}],
            )
        )
    )

    report = catalog.introspect(
        "versioned",
        target_version="FluentControl 3.5 SP1",
        version_evidence={"source": "fixture", "build": "3.5.1"},
        source_examples=[
            {"function_name": "Versioned", "entry": "z.xscr", "line": 20},
            {"function_name": "Versioned", "entry": "a.xscr", "line": 10},
            {"function_name": "Other", "entry": "ignored.xscr"},
        ],
    )

    assert report["canonical_name"] == "Versioned"
    assert report["status"] == "supported"
    assert report["category"] == "function"
    assert len(report["overloads"]) == 2
    assert report["return_types"] == ["number", "string"]
    assert [item["entry"] for item in report["source_examples"]] == ["a.xscr", "z.xscr"]
    assert catalog.introspect("Versioned", target_version="not verified")["status"] == "indeterminate"

    assert catalog.generation_policy("Versioned", target_version="3.5")["action"] == "generate"
    assert catalog.generation_policy("Versioned", target_version="4.0")["allowed"] is False
    assert catalog.generation_policy("Unknown", source_kind="generated")["allowed"] is False
    assert catalog.generation_policy("Unknown", source_kind="imported")["action"] == "preserve_source"


def test_catalog_rejects_invalid_version_ranges() -> None:
    with pytest.raises(CatalogValidationError, match="min cannot exceed max"):
        load_expression_symbol_catalog(_catalog(_symbol("Fn", version_ranges=[{"min": "4.0", "max": "3.0"}])))

    with pytest.raises(CatalogValidationError, match="dotted numeric version"):
        load_expression_symbol_catalog(_catalog(_symbol("Fn", version_ranges=[{"min": "future"}])))
