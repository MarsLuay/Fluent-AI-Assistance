"""Parser for FluentControl expression fields."""

from __future__ import annotations

from dataclasses import dataclass

from .ast import (
    BinaryExpression,
    BooleanLiteral,
    Expression,
    FunctionCall,
    NumberLiteral,
    StringLiteral,
    UnaryExpression,
    VariableReference,
)
from .lexer import ExpressionLexError, Token, lex_expression


@dataclass(frozen=True)
class ExpressionParseError(ValueError):
    reason: str
    offset: int
    source: str

    def __str__(self) -> str:
        return f"{self.reason} at offset {self.offset}"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "invalid_expression",
            "reason": self.reason,
            "offset": self.offset,
            "source": self.source,
        }


def parse_expression(source: str) -> Expression:
    try:
        tokens = lex_expression(source)
    except ExpressionLexError as exc:
        raise ExpressionParseError(exc.reason, exc.offset, exc.source) from exc
    parser = _Parser(tokens, source)
    expression = parser.parse()
    parser.expect_eof(expression)
    return expression


def try_parse_expression(source: str) -> Expression | None:
    try:
        return parse_expression(source)
    except ExpressionParseError:
        return None


class _Parser:
    def __init__(self, tokens: list[Token], source: str):
        self.tokens = tokens
        self.source = source
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.current
        if self.index < len(self.tokens) - 1:
            self.index += 1
        return token

    def parse(self) -> Expression:
        if self.current.kind == "EOF":
            raise ExpressionParseError("empty_expression", self.current.offset, self.source)
        return self.parse_or()

    def parse_or(self) -> Expression:
        left = self.parse_and()
        while self.current.kind == "IDENT" and self.current.value.casefold() == "or":
            self.advance()
            right = self.parse_and()
            left = BinaryExpression(operator="OR", left=left, right=right)
        return left

    def parse_and(self) -> Expression:
        left = self.parse_comparison()
        while self.current.kind == "IDENT" and self.current.value.casefold() == "and":
            self.advance()
            right = self.parse_comparison()
            left = BinaryExpression(operator="AND", left=left, right=right)
        return left

    def expect_eof(self, expression: Expression) -> None:
        if self.current.kind == "EOF":
            return
        if isinstance(expression, StringLiteral) and self.current.kind == "STRING":
            reason = "unexpected_quote_after_string_literal"
        else:
            reason = "unexpected_token_after_expression"
        raise ExpressionParseError(reason, self.current.offset, self.source)

    def parse_comparison(self) -> Expression:
        left = self.parse_additive()
        while self.current.kind == "OP" and self.current.value in {"=", "<>", "<", ">", "<=", ">="}:
            operator = self.advance().value
            right = self.parse_additive()
            left = BinaryExpression(operator=operator, left=left, right=right)  # type: ignore[arg-type]
        return left

    def parse_additive(self) -> Expression:
        left = self.parse_multiplicative()
        while self.current.kind == "OP" and self.current.value in {"+", "-"}:
            operator = self.advance().value
            right = self.parse_multiplicative()
            left = BinaryExpression(operator=operator, left=left, right=right)  # type: ignore[arg-type]
        return left

    def parse_multiplicative(self) -> Expression:
        left = self.parse_unary()
        while self.current.kind == "OP" and self.current.value in {"*", "/"}:
            operator = self.advance().value
            right = self.parse_unary()
            left = BinaryExpression(operator=operator, left=left, right=right)  # type: ignore[arg-type]
        return left

    def parse_unary(self) -> Expression:
        if self.current.kind == "OP" and self.current.value in {"+", "-"}:
            operator = self.advance().value
            operand = self.parse_unary()
            if isinstance(operand, NumberLiteral):
                value = operand.value if operator == "+" else -operand.value
                return NumberLiteral(value=value)
            return UnaryExpression(operator=operator, operand=operand)  # type: ignore[arg-type]
        return self.parse_power()

    def parse_power(self) -> Expression:
        left = self.parse_primary()
        if self.current.kind == "OP" and self.current.value == "^":
            self.advance()
            return BinaryExpression(operator="^", left=left, right=self.parse_unary())
        return left

    def parse_primary(self) -> Expression:
        token = self.current
        if token.kind == "STRING":
            self.advance()
            return StringLiteral(value=token.value)
        if token.kind == "NUMBER":
            self.advance()
            value = float(token.value)
            return NumberLiteral(value=int(value) if value.is_integer() else value)
        if token.kind == "IDENT":
            self.advance()
            if token.value.casefold() in {"true", "false"}:
                return BooleanLiteral(value=token.value.casefold() == "true")
            if self.current.kind == "LPAREN":
                self.advance()
                args: list[Expression] = []
                if self.current.kind != "RPAREN":
                    while True:
                        args.append(self.parse_or())
                        if self.current.kind == "COMMA":
                            self.advance()
                            continue
                        break
                if self.current.kind != "RPAREN":
                    raise ExpressionParseError("expected_closing_parenthesis", self.current.offset, self.source)
                self.advance()
                return FunctionCall(name=token.value, arguments=tuple(args))
            return VariableReference(name=token.value)
        if token.kind == "LPAREN":
            self.advance()
            expression = self.parse_or()
            if self.current.kind != "RPAREN":
                raise ExpressionParseError("expected_closing_parenthesis", self.current.offset, self.source)
            self.advance()
            return expression
        raise ExpressionParseError("expected_expression", token.offset, self.source)
