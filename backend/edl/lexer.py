"""Indentation sensitive lexer for EDL.

The token stream uses INDENT/DEDENT tokens so the parser can read
Python-style nested blocks without caring about column arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass


class LexEduError(Exception):
    pass


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    line: int
    col: int


TWO_CHAR_OPS = {"==", "!=", "<=", ">="}
ONE_CHAR_OPS = set("+-*/%<>(),:=!")
KEYWORDS = {"fun", "if", "else", "while", "return", "and", "or", "not", "true", "false", "none"}


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    indent_stack = [0]
    lines = source.splitlines()
    if source and not source.endswith("\n"):
        lines.append("")  # force a final newline handling pass

    paren_depth = 0
    for lineno, raw_line in enumerate(lines, start=1):
        # Strip comments while keeping the column information.
        line = raw_line
        hash_pos = line.find("#")
        if hash_pos >= 0:
            line = line[:hash_pos]

        stripped = line.lstrip()
        if not stripped:
            continue  # blank line / comment only

        if paren_depth == 0:
            indent = len(line) - len(stripped)
            if "\t" in line[:indent]:
                raise LexEduError(f"line {lineno}: tabs are not allowed for indentation")
            if indent > indent_stack[-1]:
                indent_stack.append(indent)
                tokens.append(Token("INDENT", indent, lineno, 0))
            while indent < indent_stack[-1]:
                indent_stack.pop()
                tokens.append(Token("DEDENT", indent, lineno, 0))
            if indent != indent_stack[-1]:
                raise LexEduError(f"line {lineno}: inconsistent indentation")

        text = line if paren_depth else stripped
        # Parenthesised continuations keep original columns; ordinary code is
        # scanned from the stripped start and columns are added back below.
        col = 0
        indent = len(line) - len(stripped)

        while col < len(text):
            ch = text[col]
            display_col = col if paren_depth else indent + col
            if ch in " \t":
                col += 1
                continue
            start = col
            start_line = lineno
            two = text[col : col + 2]
            if two in TWO_CHAR_OPS:
                tokens.append(Token("OP", two, lineno, display_col + 1))
                col += 2
            elif ch in ONE_CHAR_OPS:
                if ch == "!":
                    raise LexEduError(f"line {lineno}:{display_col + 1}: unexpected '!'")
                tokens.append(Token("OP", ch, lineno, display_col + 1))
                if ch in "(":
                    paren_depth += 1
                elif ch == ")":
                    paren_depth = max(0, paren_depth - 1)
                col += 1
            elif ch.isdigit():
                j = col
                while j < len(text) and text[j].isdigit():
                    j += 1
                tokens.append(Token("NUMBER", int(text[col:j]), lineno, display_col + 1))
                col = j
            elif ch.isalpha() or ch == "_":
                j = col
                while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                    j += 1
                word = text[col:j]
                kind = "KEYWORD" if word in KEYWORDS else "NAME"
                tokens.append(Token(kind, word, lineno, display_col + 1))
                col = j
            elif ch == '"':
                j = col + 1
                buf: list[str] = []
                while j < len(text) and text[j] != '"':
                    c = text[j]
                    if c == "\\" and j + 1 < len(text):
                        esc = text[j + 1]
                        buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(esc, esc))
                        j += 2
                    else:
                        buf.append(c)
                        j += 1
                if j >= len(text):
                    raise LexEduError(f"line {lineno}: unterminated string")
                tokens.append(Token("STRING", "".join(buf), lineno, display_col + 1))
                col = j + 1
            else:
                raise LexEduError(f"line {lineno}:{display_col + 1}: unexpected character {ch!r}")
            _ = start, start_line

        if paren_depth == 0:
            tokens.append(Token("NEWLINE", "\n", lineno, len(line) + 1))

    while len(indent_stack) > 1:
        indent_stack.pop()
        tokens.append(Token("DEDENT", 0, len(lines), 0))
    tokens.append(Token("EOF", None, len(lines), 0))
    return tokens
