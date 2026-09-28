"""Tree-walking interpreter that executes as an event generator.

The interpreter never decides whether the debugger should pause.  It only
produces an ordered stream of ``line``/``call``/``return``/``output``/``error``
events.  The debug controller in :mod:`edl.debugger` consumes that stream and
decides when to stop.

Every function activation owns a :class:`Frame` with its own ``locals`` dict,
so recursive calls can never overwrite each other's local variables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from . import ast_nodes as ast


class RuntimeEduError(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(message)
        self.line = line
        self.message = message


@dataclass
class Frame:
    name: str
    params: list[str]
    line: int
    locals: dict[str, object] = field(default_factory=dict)


class _ReturnSignal(Exception):
    def __init__(self, value: object):
        self.value = value


class Interpreter:
    def __init__(self, program: ast.Program):
        self.program = program
        self.frames: list[Frame] = []

    def run(self) -> Iterator[dict]:
        main_fn = self.program.functions["main"]
        yield {"type": "call", "name": "main", "line": main_fn.line}
        frame = Frame("main", list(main_fn.params), main_fn.line)
        self.frames.append(frame)
        try:
            yield from self._exec_stmts(main_fn.body, frame)
        except RuntimeEduError as exc:
            yield {"type": "error", "line": exc.line, "message": exc.message}
            return
        except _ReturnSignal:
            pass
        yield {"type": "return", "name": "main", "line": main_fn.line, "value": None}
        self.frames.pop()
        yield {"type": "exit", "line": main_fn.line}

    def _exec_stmts(self, statements: list, frame: Frame) -> Iterator[dict]:
        for stmt in statements:
            yield from self._exec_stmt(stmt, frame)

    def _exec_stmt(self, stmt, frame: Frame) -> Iterator[dict]:
        frame.line = stmt.line
        yield {"type": "line", "line": stmt.line}

        if isinstance(stmt, ast.Assign):
            value = yield from self._eval(stmt.value, frame)
            frame.locals[stmt.target] = value
        elif isinstance(stmt, ast.ExprStmt):
            yield from self._eval(stmt.expr, frame)
        elif isinstance(stmt, ast.If):
            condition = yield from self._eval(stmt.condition, frame)
            if self._truthy(condition, stmt.condition.line):
                yield from self._exec_stmts(stmt.body, frame)
            elif stmt.else_body is not None:
                yield from self._exec_stmts(stmt.else_body, frame)
        elif isinstance(stmt, ast.While):
            while True:
                condition = yield from self._eval(stmt.condition, frame)
                if not self._truthy(condition, stmt.condition.line):
                    break
                yield from self._exec_stmts(stmt.body, frame)
                frame.line = stmt.line
                yield {"type": "line", "line": stmt.line}
        elif isinstance(stmt, ast.Return):
            value = None
            if stmt.value is not None:
                value = yield from self._eval(stmt.value, frame)
            yield {"type": "return", "name": frame.name, "line": stmt.line, "value": value}
            raise _ReturnSignal(value)
        else:  # pragma: no cover - parser guarantees exhaustive cases
            raise RuntimeEduError(stmt.line, f"unknown statement {type(stmt).__name__}")

    def _eval(self, node, frame: Frame) -> Iterator[dict]:
        if isinstance(node, ast.Number):
            return node.value
        if isinstance(node, ast.String):
            return node.value
        if isinstance(node, ast.Bool):
            return node.value
        if isinstance(node, ast.NoneConst):
            return None
        if isinstance(node, ast.Name):
            if node.name not in frame.locals:
                raise RuntimeEduError(node.line, f"name {node.name!r} is not defined")
            return frame.locals[node.name]
        if isinstance(node, ast.Unary):
            value = yield from self._eval(node.operand, frame)
            if node.op == "-":
                self._expect_number(value, node.line)
                return -value
            if node.op == "+":
                self._expect_number(value, node.line)
                return value
            if node.op == "not":
                return not self._truthy(value, node.line)
        if isinstance(node, ast.Binary):
            return (yield from self._eval_binary(node, frame))
        if isinstance(node, ast.Call):
            return (yield from self._eval_call(node, frame))
        raise RuntimeEduError(getattr(node, "line", 0), "unsupported expression")  # pragma: no cover

    def _eval_binary(self, node: ast.Binary, frame: Frame) -> Iterator[dict]:
        if node.op == "and":
            left = yield from self._eval(node.left, frame)
            if not self._truthy(left, node.line):
                return False
            return self._truthy((yield from self._eval(node.right, frame)), node.line)
        if node.op == "or":
            left = yield from self._eval(node.left, frame)
            if self._truthy(left, node.line):
                return True
            return self._truthy((yield from self._eval(node.right, frame)), node.line)

        left = yield from self._eval(node.left, frame)
        right = yield from self._eval(node.right, frame)
        op = node.op
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op in ("<", "<=", ">", ">="):
            if isinstance(left, str) != isinstance(right, str) or isinstance(left, bool) or isinstance(right, bool):
                raise RuntimeEduError(node.line, f"cannot compare {self._type_name(left)} with {self._type_name(right)}")
            if not isinstance(left, (int, str)) or not isinstance(right, (int, str)):
                raise RuntimeEduError(node.line, "only numbers and strings can be compared")
            return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[op]
        if op in ("+", "-", "*", "/", "%"):
            if op == "+" and isinstance(left, str) and isinstance(right, str):
                return left + right
            self._expect_number(left, node.line)
            self._expect_number(right, node.line)
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if right == 0:
                raise RuntimeEduError(node.line, "division by zero")
            if op == "/":
                # Truncated integer division, towards zero.
                quotient = abs(left) // abs(right)
                return quotient if (left < 0) == (right < 0) else -quotient
            remainder = abs(left) % abs(right)
            return remainder if left >= 0 else -remainder
        raise RuntimeEduError(node.line, f"unsupported operator {op}")  # pragma: no cover

    def _eval_call(self, node: ast.Call, frame: Frame) -> Iterator[dict]:
        if node.name == "print":
            parts = []
            for arg in node.args:
                parts.append(self._format_value((yield from self._eval(arg, frame))))
            text = " ".join(parts)
            yield {"type": "output", "line": node.line, "text": text}
            return None

        fn = self.program.functions.get(node.name)
        if fn is None:
            raise RuntimeEduError(node.line, f"function {node.name!r} is not defined")
        if len(node.args) != len(fn.params):
            raise RuntimeEduError(
                node.line,
                f"function {node.name!r} expects {len(fn.params)} arguments but got {len(node.args)}",
            )
        values = []
        for arg in node.args:
            values.append((yield from self._eval(arg, frame)))

        yield {"type": "call", "name": node.name, "line": node.line}
        callee = Frame(node.name, list(fn.params), fn.line)
        callee.locals = dict(zip(fn.params, values))
        self.frames.append(callee)
        try:
            yield from self._exec_stmts(fn.body, callee)
        except _ReturnSignal as signal:
            self.frames.pop()
            return signal.value
        self.frames.pop()
        return None

    @staticmethod
    def _expect_number(value: object, line: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeEduError(line, f"a number is required but got {Interpreter._type_name(value)}")

    @staticmethod
    def _truthy(value: object, line: int) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            return len(value) > 0
        raise RuntimeEduError(line, "value cannot be used as a condition")  # pragma: no cover

    @staticmethod
    def _type_name(value: object) -> str:
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "number"
        if isinstance(value, str):
            return "string"
        if value is None:
            return "none"
        return "value"  # pragma: no cover

    @staticmethod
    def _format_value(value: object) -> str:
        if value is None:
            return "none"
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value)
