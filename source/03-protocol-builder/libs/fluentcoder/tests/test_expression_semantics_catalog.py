from __future__ import annotations

from fluentcoder.expressions import (
    ExpressionSemanticContext,
    FunctionResolution,
    FunctionSignature,
    check_expression_semantics,
    load_expression_symbol_catalog,
    parse_expression,
    parse_or_preserve_source_expression,
    semantic_context_from_variables,
)


def _symbol(
    name: str,
    *,
    signatures: list[dict],
    version_ranges: list[dict] | None = None,
    kind: str = "function",
) -> dict:
    return {
        "name": name,
        "kind": kind,
        "contexts": ["script_expression"],
        "versionRanges": version_ranges or [],
        "provenance": [{"source": "fixture", "reference": "semantic-catalog-fixture"}],
        "confidence": 0.9,
        "signatures": signatures,
    }


def _catalog(*symbols: dict):
    return load_expression_symbol_catalog(
        {
            "schema": "tecan.expression_symbols.v1",
            "schemaVersion": 1,
            "symbols": list(symbols),
        }
    )


def test_semantics_selects_catalog_overloads_and_exposes_provenance() -> None:
    catalog = _catalog(
        _symbol(
            "Overloaded",
            signatures=[
                {"argumentTypes": ["string"], "returnType": "string"},
                {"argumentTypes": ["number"], "returnType": "number"},
            ],
        )
    )

    result = check_expression_semantics(
        parse_expression("Overloaded(1)"),
        semantic_context_from_variables({}, catalog=catalog),
    )

    assert result.valid
    assert result.type_name == "number"
    resolution = result.function_resolutions[0]
    assert isinstance(resolution, FunctionResolution)
    assert resolution.status == "known_supported"
    assert resolution.category == "function"
    assert resolution.provenance[0].reference == "semantic-catalog-fixture"
    assert resolution.signature is not None
    assert resolution.signature.return_type == "number"


def test_semantics_distinguishes_version_states_and_catalog_unknowns() -> None:
    catalog = _catalog(
        _symbol(
            "Versioned",
            signatures=[{"argumentTypes": [], "returnType": "number"}],
            version_ranges=[{"min": "3.0", "max": "3.8"}],
        )
    )

    supported = check_expression_semantics(
        parse_expression("Versioned()"),
        semantic_context_from_variables({}, catalog=catalog, target_version="3.8.0"),
    )
    unsupported = check_expression_semantics(
        parse_expression("Versioned()"),
        semantic_context_from_variables({}, catalog=catalog, target_version="4.0"),
    )
    unknown_target = check_expression_semantics(
        parse_expression("Versioned()"),
        semantic_context_from_variables({}, catalog=catalog),
    )
    unknown_symbol = check_expression_semantics(
        parse_expression("NotCataloged()"),
        semantic_context_from_variables({}, catalog=catalog),
    )

    assert supported.valid
    assert supported.function_resolutions[0].status == "known_supported"
    assert not unsupported.valid
    assert unsupported.issues[0].code == "unsupported_function_version"
    assert unsupported.function_resolutions[0].status == "known_unsupported"
    assert unknown_target.valid
    assert unknown_target.issues[0].code == "function_target_version_unknown"
    assert unknown_target.issues[0].severity == "warning"
    assert unknown_target.function_resolutions[0].status == "target_version_unknown"
    assert not unknown_symbol.valid
    assert unknown_symbol.issues[0].code == "unknown_function"
    assert unknown_symbol.function_resolutions[0].status == "catalog_unknown"


def test_source_preserved_unknown_function_is_retained_with_warning_evidence() -> None:
    expression = parse_or_preserve_source_expression("VendorOnly(1")
    result = check_expression_semantics(expression)

    assert result.valid
    warning = next(issue for issue in result.issues if issue.code == "source_preserved_unknown_function")
    assert warning.severity == "warning"
    assert warning.function_name == "VendorOnly"
    assert "VendorOnly" in warning.message
    assert result.function_resolutions[0].status == "catalog_unknown"


def test_custom_function_maps_remain_explicit_and_variables_stay_separate() -> None:
    custom = {
        "Custom": FunctionSignature(
            name="Custom",
            argument_types=("number",),
            return_type="number",
        )
    }
    context = ExpressionSemanticContext(
        variables={"Custom": "String"},
        functions=custom,
        enforce_declared_variables=True,
    )

    custom_result = check_expression_semantics(parse_expression("Custom(1)"), context)
    built_in_result = check_expression_semantics(parse_expression("Round(1)"), context)

    assert custom_result.valid
    assert custom_result.function_resolutions[0].status == "custom"
    assert not built_in_result.valid
    assert built_in_result.issues[0].code == "unknown_function"


def test_catalog_backed_attribute_diagnostics_remain_contextual() -> None:
    result = check_expression_semantics(parse_expression('GetAttribute("pool_tube[cycle]", "Barcode")'))

    assert result.valid
    assert result.issues[0].code == "literal_dynamic_labware_reference"
    assert result.function_resolutions[0].category == "function"
