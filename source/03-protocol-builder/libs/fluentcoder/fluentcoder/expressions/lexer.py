"""Small lexer for FluentControl expression fields."""

from __future__ import annotations

from dataclasses import dataclass


class ExpressionLexError(ValueError):
    def __init__(self, reason: str, offset: int, source: str):
        self.reason = reason
        self.offset = offset
        self.source = source
        super().__init__(f"{reason} at offset {offset}")


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    offset: int


_SINGLE_CHAR_TOKENS = {
    "(": "LPAREN",
    ")": "RPAREN",
    ",": "COMMA",
    "+": "OP",
    "-": "OP",
    "*": "OP",
    "/": "OP",
    "^": "OP",
    "=": "OP",
}


def lex_expression(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    length = len(source)
    while i < length:
        char = source[i]
        if char.isspace():
            i += 1
            continue
        if char == '"':
            token, i = _lex_string(source, i)
            tokens.append(token)
            continue
        if char.isdigit() or (char == "." and i + 1 < length and source[i + 1].isdigit()):
            token, i = _lex_number(source, i)
            tokens.append(token)
            continue
        if char.isalpha() or char == "_":
            start = i
            i += 1
            while i < length and (source[i].isalnum() or source[i] == "_"):
                i += 1
            if i < length and source[i] == "[":
                i = _consume_index_suffix(source, i)
            tokens.append(Token("IDENT", source[start:i], start))
            continue
        if char in "<>":
            start = i
            if i + 1 < length and source[i:i + 2] in {"<=", ">=", "<>"}:
                tokens.append(Token("OP", source[i:i + 2], start))
                i += 2
            else:
                tokens.append(Token("OP", char, start))
                i += 1
            continue
        if char == "&":
            # FluentControl accepts `&` as the compact logical-AND spelling.
            # Normalize it into the parser's canonical textual operator while
            # retaining the source language's boolean precedence.
            tokens.append(Token("IDENT", "and", i))
            i += 1
            continue
        token_kind = _SINGLE_CHAR_TOKENS.get(char)
        if token_kind:
            tokens.append(Token(token_kind, char, i))
            i += 1
            continue
        raise ExpressionLexError("unexpected_character", i, source)
    tokens.append(Token("EOF", "", length))
    return tokens


def _consume_index_suffix(source: str, start: int) -> int:
    depth = 0
    i = start
    while i < len(source):
        char = source[i]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        elif char in "\r\n":
            raise ExpressionLexError("newline_in_index_reference", i, source)
        i += 1
    raise ExpressionLexError("unterminated_index_reference", start, source)


def _lex_string(source: str, start: int) -> tuple[Token, int]:
    chars: list[str] = []
    i = start + 1
    while i < len(source):
        char = source[i]
        if char == '"':
            return Token("STRING", "".join(chars), start), i + 1
        if char in "\r\n":
            raise ExpressionLexError("newline_in_string_literal", i, source)
        if char == "\\":
            if i + 1 >= len(source):
                raise ExpressionLexError("unterminated_escape_sequence", i, source)
            nxt = source[i + 1]
            escapes = {
                '"': '"',
                "\\": "\\",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }
            if nxt not in escapes:
                raise ExpressionLexError("invalid_escape_sequence", i, source)
            chars.append(escapes[nxt])
            i += 2
            continue
        chars.append(char)
        i += 1
    raise ExpressionLexError("unterminated_string_literal", start, source)


def _lex_number(source: str, start: int) -> tuple[Token, int]:
    i = start
    seen_dot = False
    seen_digit = False
    while i < len(source):
        char = source[i]
        if char == "." and not seen_dot:
            seen_dot = True
            i += 1
            continue
        if char.isdigit():
            seen_digit = True
            i += 1
            continue
        break
    if i < len(source) and source[i] in {"e", "E"}:
        exp_start = i
        i += 1
        if i < len(source) and source[i] in {"+", "-"}:
            i += 1
        digit_start = i
        while i < len(source) and source[i].isdigit():
            i += 1
        if i == digit_start:
            raise ExpressionLexError("invalid_number_exponent", exp_start, source)
    if not seen_digit:
        raise ExpressionLexError("invalid_number_literal", start, source)
    return Token("NUMBER", source[start:i], start), i
