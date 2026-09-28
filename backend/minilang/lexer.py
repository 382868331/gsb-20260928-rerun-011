from __future__ import annotations

from dataclasses import dataclass


class LexError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(f"第 {line} 行: {message}")
        self.line = line


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    line: int


KEYWORDS = {
    "let": "LET",
    "if": "IF",
    "else": "ELSE",
    "while": "WHILE",
    "fun": "FUN",
    "return": "RETURN",
    "true": "BOOL",
    "false": "BOOL",
    "and": "AND",
    "or": "OR",
    "not": "NOT",
}

_TWO_CHAR = {
    "==": "EQ",
    "!=": "NE",
    "<=": "LE",
    ">=": "GE",
}
_ONE_CHAR = set("+-*/%<>()=,:")


class Lexer:
    """Tokenizes Python-style indentation into INDENT/DEDENT/NEWLINE tokens."""

    def tokenize(self, source: str) -> list[Token]:
        raw_lines = source.splitlines()
        tokens: list[Token] = []
        indents: list[int] = [0]

        for lineno, raw in enumerate(raw_lines, start=1):
            # Strip comments and trailing whitespace.
            line = raw
            hash_pos = line.find("#")
            if hash_pos != -1:
                line = line[:hash_pos]
            stripped = line.strip()
            if not stripped:
                continue

            spaces = len(line) - len(line.lstrip(" \t"))
            prefix = line[:spaces]
            if "\t" in prefix:
                raise LexError("缩进不能使用制表符", lineno)

            if spaces > indents[-1]:
                indents.append(spaces)
                tokens.append(Token("INDENT", spaces, lineno))
            while spaces < indents[-1]:
                indents.pop()
                tokens.append(Token("DEDENT", None, lineno))
            if spaces != indents[-1]:
                raise LexError("缩进层次不一致", lineno)

            col = spaces
            while col < len(line):
                ch = line[col]
                if ch in " \t":
                    col += 1
                    continue
                if ch.isalpha() or ch == "_":
                    start = col
                    while col < len(line) and (line[col].isalnum() or line[col] == "_"):
                        col += 1
                    word = line[start:col]
                    kind = KEYWORDS.get(word, "IDENT")
                    value: object = word
                    if kind == "BOOL":
                        value = word == "true"
                    tokens.append(Token(kind, value, lineno))
                    continue
                if ch.isdigit():
                    start = col
                    while col < len(line) and line[col].isdigit():
                        col += 1
                    tokens.append(Token("INT", int(line[start:col]), lineno))
                    continue
                two = line[col:col + 2]
                if two in _TWO_CHAR:
                    tokens.append(Token(_TWO_CHAR[two], two, lineno))
                    col += 2
                    continue
                if ch in _ONE_CHAR:
                    tokens.append(Token(ch, ch, lineno))
                    col += 1
                    continue
                raise LexError(f"无法识别的字符 {ch!r}", lineno)

            tokens.append(Token("NEWLINE", "\n", lineno))

        while len(indents) > 1:
            indents.pop()
            tokens.append(Token("DEDENT", None, len(raw_lines) or 1))
        tokens.append(Token("EOF", None, len(raw_lines) or 1))
        return tokens
