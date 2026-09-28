"""AST node definitions for EDL."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Number:
    value: int
    line: int


@dataclass
class String:
    value: str
    line: int


@dataclass
class Bool:
    value: bool
    line: int


@dataclass
class NoneConst:
    line: int


@dataclass
class Name:
    name: str
    line: int


@dataclass
class Binary:
    op: str
    left: object
    right: object
    line: int


@dataclass
class Unary:
    op: str
    operand: object
    line: int


@dataclass
class Call:
    name: str
    args: list
    line: int


@dataclass
class Assign:
    target: str
    value: object
    line: int


@dataclass
class ExprStmt:
    expr: object
    line: int


@dataclass
class If:
    condition: object
    body: list
    else_body: list | None
    line: int


@dataclass
class While:
    condition: object
    body: list
    line: int


@dataclass
class Return:
    value: object | None
    line: int


@dataclass
class Function:
    name: str
    params: list[str]
    body: list
    line: int


@dataclass
class Program:
    functions: dict[str, Function] = field(default_factory=dict)
    entry_line: int = 0
