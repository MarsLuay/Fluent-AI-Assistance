"""Semantic validation for FluentControl expression AST nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from .ast import (
    BinaryExpression,
    BooleanLiteral,
    Expression,
    FunctionCall,
    NumberLiteral,
    ReviewedRawExpression,
    SourcePreservedExpression,
    StringLiteral,
    UnaryExpression,
    VariableReference,
    expression_kind,
)
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

    @property
    def expression_type(self) -> ExpressionType:
        return normalize_fluent_type_name(self.type_name)


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
        base_name = str(name or "").split("[", 1)[0]
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
    return SemanticResult(type_name=inferred_type, issues=tuple(issues))


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
    return signature.return_type


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
