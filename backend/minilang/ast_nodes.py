from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    line: int = 0


@dataclass
class IntLit(Node):
    value: int = 0


@dataclass
class BoolLit(Node):
    value: bool = False


@dataclass
class Var(Node):
    name: str = ""


@dataclass
class BinOp(Node):
    op: str = ""
    left: Node | None = None
    right: Node | None = None


@dataclass
class UnaryOp(Node):
    op: str = ""
    operand: Node | None = None


@dataclass
class Call(Node):
    name: str = ""
    args: list[Node] = field(default_factory=list)


@dataclass
class Let(Node):
    name: str = ""
    value: Node | None = None


@dataclass
class Assign(Node):
    name: str = ""
    value: Node | None = None


@dataclass
class ExprStmt(Node):
    expr: Node | None = None


@dataclass
class If(Node):
    cond: Node | None = None
    then_body: list[Node] = field(default_factory=list)
    else_body: list[Node] = field(default_factory=list)


@dataclass
class While(Node):
    cond: Node | None = None
    body: list[Node] = field(default_factory=list)


@dataclass
class Return(Node):
    value: Node | None = None


@dataclass
class FunDef(Node):
    name: str = ""
    params: list[str] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)


@dataclass
class Program(Node):
    functions: dict[str, FunDef] = field(default_factory=dict)
    main_body: list[Node] = field(default_factory=list)
