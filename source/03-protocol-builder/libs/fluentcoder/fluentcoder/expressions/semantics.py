"""Semantic validation for FluentControl expression AST nodes."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import Any, Literal, Mapping

from .ast import (
    BinaryExpression,
    BooleanLiteral,
    Expression,
    FunctionCall,
    IndexExpression,
    NumberLiteral,
    ReviewedRawExpression,
    SourcePreservedExpression,
    StringLiteral,
    UnaryExpression,
    VariableReference,
    expression_kind,
)
from .attributes import attribute_references_in_expression, AttributeReference
from .operators import (
    ExpectedTypeName as ExpectedType,
    ExpressionTypeName as ExpressionType,
    binary_operator_definition,
    format_expected_type,
    infer_binary_operator_type,
    is_type_compatible,
)


@dataclass(frozen=True)
class VariableSymbol:
    name: str
    type_name: str = "unknown"
    is_array: bool | None = None
    array_size: int | None = None
    scope: str = "Script"
    original_name: str = ""

    def __post_init__(self) -> None:
        declared_name = str(self.name or "").strip()
        base_name, declared_size = _split_array_declaration(declared_name)
        type_text = str(self.type_name or "").strip()
        type_is_array = type_text.endswith("[]")
        if type_is_array:
            type_text = type_text[:-2].strip()
        explicit_array = self.is_array
        declared_array = "[" in declared_name and declared_name.endswith("]")
        array = bool(explicit_array) if explicit_array is not None else bool(declared_array or type_is_array)
        size = self.array_size if self.array_size is not None else declared_size
        if size is not None and size < 0:
            raise ValueError("array_size must be non-negative")
        object.__setattr__(self, "name", base_name)
        object.__setattr__(self, "type_name", type_text or "unknown")
        object.__setattr__(self, "is_array", array)
        object.__setattr__(self, "array_size", size)
        object.__setattr__(self, "original_name", self.original_name or declared_name)

    @property
    def element_type_name(self) -> str:
        return self.type_name

    @property
    def expression_type(self) -> ExpressionType:
        return normalize_fluent_type_name(self.element_type_name)

    @property
    def bounds(self) -> tuple[int, int] | None:
        if self.array_size is None:
            return None
        return (0, self.array_size - 1)


@dataclass(frozen=True)
class FunctionSignature:
    name: str
    argument_types: tuple[ExpectedType, ...] = ()
    return_type: ExpressionType = "unknown"
    variadic_type: ExpectedType | None = None
    min_arguments: int | None = None
    max_arguments: int | None = None

    def expected_argument_type(self, index: int) -> ExpectedType | None:
        if index < len(self.argument_types):
            return self.argument_types[index]
        return self.variadic_type

    @property
    def min_count(self) -> int:
        if self.min_arguments is not None:
            return self.min_arguments
        return len(self.argument_types)

    @property
    def max_count(self) -> int | None:
        if self.max_arguments is not None:
            return self.max_arguments
        if self.variadic_type is not None:
            return None
        return len(self.argument_types)


@dataclass(frozen=True)
class SemanticIssue:
    code: str
    message: str
    path: str = "$"
    severity: Literal["error", "warning"] = "error"
    expected_type: str | None = None
    actual_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "message": self.message,
                "path": self.path,
                "severity": self.severity,
                "expected_type": self.expected_type,
                "actual_type": self.actual_type,
            }.items()
            if value not in (None, "")
        }


@dataclass(frozen=True)
class SemanticResult:
    type_name: ExpressionType
    issues: tuple[SemanticIssue, ...] = ()
    attribute_references: tuple[AttributeReference, ...] = ()

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


@dataclass(frozen=True)
class ExpressionSemanticContext:
    variables: Mapping[str, VariableSymbol] | None = None
    functions: Mapping[str, FunctionSignature] | None = None
    enforce_declared_variables: bool = False

    def __post_init__(self) -> None:
        variables = self.variables or {}
        normalized_variables: dict[str, VariableSymbol] = {}
        for name, symbol in variables.items():
            if isinstance(symbol, VariableSymbol):
                variable = symbol
            else:
                variable = VariableSymbol(name=str(name), type_name=str(symbol))
            normalized_variables[variable.name] = variable
        object.__setattr__(self, "variables", normalized_variables)

        functions = self.functions or default_function_signatures()
        normalized_functions: dict[str, FunctionSignature] = {}
        for name, signature in functions.items():
            normalized_functions[str(name).casefold()] = signature
        object.__setattr__(self, "functions", normalized_functions)

    def variable(self, name: str) -> VariableSymbol | None:
        variables = dict(self.variables or {})
        symbol = variables.get(name)
        if symbol is not None:
            return symbol
        base_name = str(name or "").split("[", 1)[0].strip()
        if base_name != name:
            return variables.get(base_name)
        return None

    def function(self, name: str) -> FunctionSignature | None:
        return dict(self.functions or {}).get(name.casefold())


def semantic_context_from_variables(
    variables: Mapping[str, str | VariableSymbol] | None,
    *,
    enforce_declared_variables: bool | None = None,
    functions: Mapping[str, FunctionSignature] | None = None,
) -> ExpressionSemanticContext:
    normalized: dict[str, VariableSymbol] = {}
    for name, value in (variables or {}).items():
        if isinstance(value, VariableSymbol):
            normalized[value.name] = value
        else:
            normalized[str(name)] = VariableSymbol(name=str(name), type_name=str(value))
    return ExpressionSemanticContext(
        variables=normalized,
        functions=functions,
        enforce_declared_variables=bool(normalized) if enforce_declared_variables is None else enforce_declared_variables,
    )


def check_expression_semantics(
    expression: Expression,
    context: ExpressionSemanticContext | None = None,
    *,
    expected_type: ExpectedType | None = None,
    assignment_target: str | None = None,
) -> SemanticResult:
    ctx = context or ExpressionSemanticContext()
    issues: list[SemanticIssue] = []
    inferred_type = _infer_expression_type(expression, ctx, "$", issues)
    if expected_type is not None and not is_type_compatible(inferred_type, expected_type):
        label = format_expected_type(expected_type)
        target = f" to {assignment_target!r}" if assignment_target else ""
        issues.append(
            SemanticIssue(
                code="assignment_type_mismatch",
                message=f"Assignment{target} expects {label}, got {inferred_type}.",
                expected_type=label,
                actual_type=inferred_type,
            )
        )
    references = []
    for reference in attribute_references_in_expression(expression):
        if reference.value is not None:
            value_issues: list[SemanticIssue] = []
            references.append(
                replace(
                    reference,
                    value_type=_infer_expression_type(reference.value, ctx, "$.attribute_value", value_issues),
                )
            )
        else:
            references.append(reference)
    return SemanticResult(
        type_name=inferred_type,
        issues=tuple(issues),
        attribute_references=tuple(references),
    )


def normalize_fluent_type_name(type_name: str | None) -> ExpressionType:
    text = str(type_name or "").strip().casefold()
    if not text:
        return "unknown"
    text = text.replace("system.", "")
    if text in {"string", "str", "text"}:
        return "string"
    if text in {
        "integer",
        "int",
        "int16",
        "int32",
        "int64",
        "short",
        "long",
        "floating point",
        "float",
        "single",
        "double",
        "decimal",
        "number",
        "numeric",
    }:
        return "number"
    if text in {"boolean", "bool"}:
        return "boolean"
    if text == "object":
        return "any"
    return "unknown"


def default_function_signatures() -> dict[str, FunctionSignature]:
    any_type: ExpectedType = "any"
    signatures = [
        FunctionSignature("concat", return_type="string", variadic_type=any_type, min_arguments=1),
        FunctionSignature("SetAttribute", argument_types=("string", "string", any_type), return_type="string"),
        FunctionSignature("GetAttribute", argument_types=("string", "string"), return_type="string"),
        FunctionSignature("MountedFESfinger", return_type="string"),
        FunctionSignature("GetCoverSiteName", argument_types=("string",), return_type="string"),
        FunctionSignature("GetCoverSiteIndex", argument_types=("string",), return_type="number"),
        FunctionSignature("GetLocationName", argument_types=("string",), return_type="string"),
        FunctionSignature("GetLocationIndex", argument_types=("string",), return_type="number"),
        FunctionSignature("Abs", argument_types=("number",), return_type="number"),
        FunctionSignature("Ceil", argument_types=("number",), return_type="number"),
        FunctionSignature("Round", argument_types=("number",), return_type="number", min_arguments=1, max_arguments=2),
        FunctionSignature("Int", argument_types=("number",), return_type="number"),
        FunctionSignature("Fix", argument_types=("number",), return_type="number"),
        FunctionSignature("Sqr", argument_types=("number",), return_type="number"),
        FunctionSignature("Len", argument_types=("string",), return_type="number"),
        FunctionSignature("Left", argument_types=("string", "number"), return_type="string"),
        FunctionSignature("Right", argument_types=("string", "number"), return_type="string"),
        FunctionSignature("Mid", argument_types=("string", "number", "number"), return_type="string", min_arguments=2, max_arguments=3),
        FunctionSignature("substring", argument_types=("string", "number", "number"), return_type="string"),
        FunctionSignature("Replace", argument_types=("string", "string", "string"), return_type="string"),
        FunctionSignature("Trim", argument_types=("string",), return_type="string"),
        FunctionSignature("LCase", argument_types=("string",), return_type="string"),
        FunctionSignature("UCase", argument_types=("string",), return_type="string"),
        FunctionSignature("CStr", argument_types=(any_type,), return_type="string"),
        FunctionSignature("Str", argument_types=(any_type,), return_type="string"),
        FunctionSignature("CDbl", argument_types=(any_type,), return_type="number"),
        FunctionSignature("CInt", argument_types=(any_type,), return_type="number"),
        FunctionSignature("Val", argument_types=("string",), return_type="number"),
        FunctionSignature("IsNumeric", argument_types=(any_type,), return_type="boolean"),
        FunctionSignature("If", argument_types=("boolean", any_type, any_type), return_type="any"),
        FunctionSignature("IIf", argument_types=("boolean", any_type, any_type), return_type="any"),
    ]
    return {signature.name.casefold(): signature for signature in signatures}


def _infer_expression_type(
    expression: Expression,
    context: ExpressionSemanticContext,
    path: str,
    issues: list[SemanticIssue],
) -> ExpressionType:
    if isinstance(expression, StringLiteral):
        return "string"
    if isinstance(expression, NumberLiteral):
        return "number"
    if isinstance(expression, BooleanLiteral):
        return "boolean"
    if isinstance(expression, VariableReference):
        symbol = context.variable(expression.name)
        if symbol is None:
            if context.enforce_declared_variables:
                issues.append(
                    SemanticIssue(
                        code="undefined_variable",
                        message=f"Variable {expression.name!r} is not declared.",
                        path=path,
                    )
                )
            return "unknown"
        return symbol.expression_type
    if isinstance(expression, IndexExpression):
        return _infer_index_expression_type(expression, context, path, issues)
    if isinstance(expression, FunctionCall):
        return _infer_function_call_type(expression, context, path, issues)
    if isinstance(expression, UnaryExpression):
        operand_type = _infer_expression_type(expression.operand, context, f"{path}.operand", issues)
        if not is_type_compatible(operand_type, "number"):
            issues.append(
                SemanticIssue(
                    code="invalid_unary_operand",
                    message=f"Unary operator {expression.operator!r} requires a number operand, got {operand_type}.",
                    path=path,
                    expected_type="number",
                    actual_type=operand_type,
                )
            )
        return "number" if operand_type != "unknown" else "unknown"
    if isinstance(expression, BinaryExpression):
        return _infer_binary_expression_type(expression, context, path, issues)
    if isinstance(expression, (SourcePreservedExpression, ReviewedRawExpression)):
        issues.append(
            SemanticIssue(
                code="raw_expression_not_semantically_validated",
                message=f"{expression_kind(expression)} cannot be semantically validated until parsed into typed AST nodes.",
                path=path,
                severity="warning",
            )
        )
        return "unknown"
    issues.append(
        SemanticIssue(
            code="unsupported_expression_node",
            message=f"Unsupported expression node {type(expression).__name__}.",
            path=path,
        )
    )
    return "unknown"


def _infer_index_expression_type(
    expression: IndexExpression,
    context: ExpressionSemanticContext,
    path: str,
    issues: list[SemanticIssue],
) -> ExpressionType:
    base = expression.base
    symbol = context.variable(base.name) if isinstance(base, VariableReference) else None
    if isinstance(base, VariableReference) and symbol is None:
        if context.enforce_declared_variables:
            issues.append(
                SemanticIssue(
                    code="undefined_variable",
                    message=f"Variable {base.name!r} is not declared.",
                    path=f"{path}.base",
                )
            )
    elif symbol is not None and not symbol.is_array:
        issues.append(
            SemanticIssue(
                code="indexed_scalar_variable",
                message=f"Variable {base.name!r} is scalar and cannot be indexed.",
                path=f"{path}.base",
            )
        )
    elif not isinstance(base, VariableReference):
        _infer_expression_type(base, context, f"{path}.base", issues)

    index_type = _infer_expression_type(expression.index, context, f"{path}.index", issues)
    if not is_type_compatible(index_type, "number"):
        issues.append(
            SemanticIssue(
                code="array_index_type_mismatch",
                message=f"Array index must be integer-compatible, got {index_type}.",
                path=f"{path}.index",
                expected_type="integer-compatible number",
                actual_type=index_type,
            )
        )
    elif isinstance(expression.index, NumberLiteral) and not float(expression.index.value).is_integer():
        issues.append(
            SemanticIssue(
                code="array_index_type_mismatch",
                message=f"Array index {expression.index.value!r} is not an integer.",
                path=f"{path}.index",
                expected_type="integer-compatible number",
                actual_type="number",
            )
        )
    elif isinstance(expression.index, VariableReference):
        index_symbol = context.variable(expression.index.name)
        if index_symbol is not None and not _is_integer_type(index_symbol.type_name):
            issues.append(
                SemanticIssue(
                    code="array_index_type_mismatch",
                    message=f"Array index variable {expression.index.name!r} must be integer-compatible.",
                    path=f"{path}.index",
                    expected_type="integer-compatible number",
                    actual_type=index_symbol.type_name,
                )
            )

    if symbol is not None and symbol.is_array and symbol.array_size is not None:
        if isinstance(expression.index, NumberLiteral) and float(expression.index.value).is_integer():
            index = int(expression.index.value)
            if index < 0 or index >= symbol.array_size:
                issues.append(
                    SemanticIssue(
                        code="array_index_out_of_bounds",
                        message=(
                            f"Array index {index} is outside {base.name!r} bounds "
                            f"0..{symbol.array_size - 1}."
                        ),
                        path=f"{path}.index",
                    )
                )
        # Unknown/dynamic indexes intentionally remain unchecked at compile time.
    return symbol.expression_type if symbol is not None and symbol.is_array else "unknown"


def _split_array_declaration(name: str) -> tuple[str, int | None]:
    text = str(name or "").strip()
    if text.endswith("[]"):
        return text[:-2].strip(), None
    if text.endswith("]") and "[" in text:
        base, raw_size = text.rsplit("[", 1)
        raw_size = raw_size[:-1].strip()
        if raw_size.isdigit():
            return base.strip(), int(raw_size)
    return text, None


def _is_integer_type(type_name: str) -> bool:
    text = str(type_name or "").casefold().replace("system.", "").strip()
    return text in {"integer", "int", "int16", "int32", "int64", "short", "long"}


def _infer_function_call_type(
    expression: FunctionCall,
    context: ExpressionSemanticContext,
    path: str,
    issues: list[SemanticIssue],
) -> ExpressionType:
    signature = context.function(expression.name)
    argument_types = [
        _infer_expression_type(argument, context, f"{path}.arguments[{index}]", issues)
        for index, argument in enumerate(expression.arguments)
    ]
    if signature is None:
        issues.append(
            SemanticIssue(
                code="unknown_function",
                message=f"Function {expression.name!r} is not in the FluentControl expression registry.",
                path=path,
            )
        )
        return "unknown"

    count = len(expression.arguments)
    max_count = signature.max_count
    if count < signature.min_count or (max_count is not None and count > max_count):
        if max_count is None:
            expected = f"at least {signature.min_count}"
        elif signature.min_count == max_count:
            expected = str(signature.min_count)
        else:
            expected = f"{signature.min_count} to {max_count}"
        issues.append(
            SemanticIssue(
                code="function_argument_count_mismatch",
                message=f"Function {expression.name!r} expects {expected} argument(s), got {count}.",
                path=path,
                expected_type=expected,
                actual_type=str(count),
            )
        )

    for index, actual_type in enumerate(argument_types):
        expected_type = signature.expected_argument_type(index)
        if expected_type is None or is_type_compatible(actual_type, expected_type):
            continue
        expected = format_expected_type(expected_type)
        issues.append(
            SemanticIssue(
                code="function_argument_type_mismatch",
                message=(
                    f"Function {expression.name!r} argument {index + 1} expects "
                    f"{expected}, got {actual_type}."
                ),
                path=f"{path}.arguments[{index}]",
                expected_type=expected,
                actual_type=actual_type,
            )
        )
    if expression.name.casefold() in {"getattribute", "setattribute"} and expression.arguments:
        labware_argument = expression.arguments[0]
        if isinstance(labware_argument, StringLiteral) and _looks_like_dynamic_labware_literal(labware_argument.value):
            issues.append(
                SemanticIssue(
                    code="literal_dynamic_labware_reference",
                    message=(
                        f"{expression.name} receives literal labware name {labware_argument.value!r}; "
                        "bracketed variable syntax inside a quoted string is not interpolated by FluentControl. "
                        "Construct a runtime string expression instead."
                    ),
                    path=f"{path}.arguments[0]",
                    severity="warning",
                )
            )
    return signature.return_type


def _looks_like_dynamic_labware_literal(value: str) -> bool:
    import re

    return bool(re.search(r"\[[A-Za-z_][A-Za-z0-9_]*(?:\s*[+\-*/^]\s*[^\]]+)?\]", str(value or "")))


def _infer_binary_expression_type(
    expression: BinaryExpression,
    context: ExpressionSemanticContext,
    path: str,
    issues: list[SemanticIssue],
) -> ExpressionType:
    left_type = _infer_expression_type(expression.left, context, f"{path}.left", issues)
    right_type = _infer_expression_type(expression.right, context, f"{path}.right", issues)
    operator = expression.operator
    definition = binary_operator_definition(operator)
    type_result = infer_binary_operator_type(operator, left_type, right_type)

    if definition is None:
        issues.append(
            SemanticIssue(
                code=type_result.issue_code,
                message=type_result.message,
                path=path,
            )
        )
        return type_result.result_type

    if definition.operand_type is not None:
        _require_binary_operands(expression, left_type, right_type, definition.operand_type, path, issues)
        if definition.category == "numeric" and (left_type != "number" or right_type != "number"):
            return "unknown"

    if not type_result.valid:
        issues.append(
            SemanticIssue(
                code=type_result.issue_code,
                message=type_result.message,
                path=path,
                expected_type=type_result.expected_type,
                actual_type=type_result.actual_type,
            )
        )
    return type_result.result_type


def _require_binary_operands(
    expression: BinaryExpression,
    left_type: ExpressionType,
    right_type: ExpressionType,
    expected_type: ExpectedType,
    path: str,
    issues: list[SemanticIssue],
) -> None:
    if not is_type_compatible(left_type, expected_type):
        issues.append(
            SemanticIssue(
                code="invalid_binary_operand",
                message=f"Operator {expression.operator!r} left operand expects {format_expected_type(expected_type)}, got {left_type}.",
                path=f"{path}.left",
                expected_type=format_expected_type(expected_type),
                actual_type=left_type,
            )
        )
    if not is_type_compatible(right_type, expected_type):
        issues.append(
            SemanticIssue(
                code="invalid_binary_operand",
                message=f"Operator {expression.operator!r} right operand expects {format_expected_type(expected_type)}, got {right_type}.",
                path=f"{path}.right",
                expected_type=format_expected_type(expected_type),
                actual_type=right_type,
            )
        )
