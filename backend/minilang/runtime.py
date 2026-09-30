from __future__ import annotations

from dataclasses import dataclass, field

from .ast_nodes import (
    Assign,
    BinOp,
    BoolLit,
    Call,
    ExprStmt,
    FunDef,
    If,
    IntLit,
    Let,
    Program,
    Return,
    UnaryOp,
    Var,
    While,
)


class RuntimeError_(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(f"第 {line} 行: {message}")
        self.message = message
        self.line = line


class TraceLimitError(RuntimeError_):
    pass


@dataclass
class Frame:
    uid: int
    name: str
    line: int
    locals: dict[str, object] = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {
            "uid": self.uid,
            "name": self.name,
            "line": self.line,
            "locals": [
                {"name": k, "value": _value_to_json(v)}
                for k, v in self.locals.items()
            ],
        }


def _value_to_json(value: object):
    if isinstance(value, bool):
        return {"type": "bool", "display": "true" if value else "false"}
    if isinstance(value, int):
        return {"type": "int", "display": str(value)}
    return {"type": "nil", "display": "nil"}


class _ReturnSignal(Exception):
    def __init__(self, value: object):
        self.value = value


MAX_EVENTS = 200_000


class RuntimeEngine:
    """Executes a Program and records a full event trace.

    Pause points are ``line`` events emitted before each statement.
    Each event carries a full call-stack snapshot, so paused-state reads
    are pure lookups that never advance execution.
    """

    def __init__(self, program: Program):
        self.program = program
        self.events: list[dict] = []
        self.frames: list[Frame] = []
        self._next_uid = 1
        self._output_parts: list[str] = []
        self._stmt_level = 0

    def run(self) -> list[dict]:
        try:
            self._push_frame("main", 1)
            self._exec_stmts(self.program.main_body)
        except _ReturnSignal:
            err = RuntimeError_("顶层代码不能使用 return", 1)
            self._append_terminal({
                "kind": "error", "line": err.line, "message": err.message,
            })
            return self.events
        except RuntimeError_ as exc:
            self._append_terminal({
                "kind": "error",
                "line": exc.line,
                "message": exc.message,
            })
            return self.events
        self._emit({"kind": "end", "line": self.frames[-1].line if self.frames else 1})
        return self.events

    # ---- trace helpers ----

    def _emit(self, extra: dict) -> None:
        if len(self.events) >= MAX_EVENTS:
            raise RuntimeError_(
                "执行事件超过上限（可能是无限循环）", extra.get("line", 1)
            )
        event = {
            "index": len(self.events),
            "output": "".join(self._output_parts),
            "stack": [f.snapshot() for f in self.frames],
        }
        event.update(extra)
        self.events.append(event)

    def _append_terminal(self, extra: dict) -> None:
        event = {
            "index": len(self.events),
            "output": "".join(self._output_parts),
            "stack": [f.snapshot() for f in self.frames],
        }
        event.update(extra)
        self.events.append(event)

    def _push_frame(self, name: str, line: int) -> Frame:
        frame = Frame(uid=self._next_uid, name=name, line=line)
        self._next_uid += 1
        self.frames.append(frame)
        return frame

    def _current(self) -> Frame:
        return self.frames[-1]

    # ---- statements ----

    def _exec_stmts(self, stmts: list) -> None:
        for stmt in stmts:
            self._exec_stmt(stmt)

    def _exec_stmt(self, stmt) -> None:
        frame = self._current()
        frame.line = stmt.line
        if isinstance(stmt, FunDef):
            return
        # Every executable statement is a potential pause point.
        self._stmt_level += 1
        self._emit({
            "kind": "line",
            "line": stmt.line,
            "level": self._stmt_level,
            "repeated": False,
        })
        try:
            if isinstance(stmt, Let):
                if stmt.name in frame.locals:
                    raise RuntimeError_(f"变量 {stmt.name} 已声明", stmt.line)
                frame.locals[stmt.name] = self._eval(stmt.value)
            elif isinstance(stmt, Assign):
                if stmt.name not in frame.locals:
                    raise RuntimeError_(f"变量 {stmt.name} 尚未声明", stmt.line)
                frame.locals[stmt.name] = self._eval(stmt.value)
            elif isinstance(stmt, ExprStmt):
                self._eval(stmt.expr)
            elif isinstance(stmt, If):
                if _is_truthy(self._eval(stmt.cond)):
                    self._exec_stmts(stmt.then_body)
                else:
                    self._exec_stmts(stmt.else_body)
            elif isinstance(stmt, While):
                while _is_truthy(self._eval(stmt.cond)):
                    self._exec_stmts(stmt.body)
                    frame.line = stmt.line
                    # Same while statement re-checking its condition:
                    # marked so step-over pairing does not count it as nested.
                    self._emit({
                        "kind": "line",
                        "line": stmt.line,
                        "level": self._stmt_level,
                        "repeated": True,
                    })
            elif isinstance(stmt, Return):
                value = self._eval(stmt.value) if stmt.value is not None else 0
                raise _ReturnSignal(value)
            else:
                raise RuntimeError_("未知语句", stmt.line)
        finally:
            self._stmt_level -= 1
            self._emit({
                "kind": "stmt_end",
                "line": stmt.line,
                "level": self._stmt_level + 1,
            })

    # ---- expressions ----

    def _eval(self, node) -> object:
        if isinstance(node, IntLit):
            return node.value
        if isinstance(node, BoolLit):
            return node.value
        if isinstance(node, Var):
            for frame in reversed(self.frames):
                if node.name in frame.locals:
                    return frame.locals[node.name]
            raise RuntimeError_(f"变量 {node.name} 未定义", node.line)
        if isinstance(node, UnaryOp):
            value = self._eval(node.operand)
            if node.op == "-":
                _expect_int(value, node.line)
                return -value
            if node.op == "not":
                return not _is_truthy(value)
        if isinstance(node, BinOp):
            if node.op == "and":
                left = self._eval(node.left)
                return self._eval(node.right) if _is_truthy(left) else left
            if node.op == "or":
                left = self._eval(node.left)
                return left if _is_truthy(left) else self._eval(node.right)
            left = self._eval(node.left)
            right = self._eval(node.right)
            return self._binary(node.op, left, right, node.line)
        if isinstance(node, Call):
            if node.name == "print":
                if len(node.args) != 1:
                    raise RuntimeError_("print 需要恰好 1 个参数", node.line)
                value = self._eval(node.args[0])
                text_value = _display(value)
                self._output_parts.append(text_value + "\n")
                self._emit({
                    "kind": "print",
                    "line": node.line,
                    "text": text_value,
                })
                return value
            fn = self.program.functions.get(node.name)
            if fn is None:
                raise RuntimeError_(f"函数 {node.name} 未定义", node.line)
            if len(node.args) != len(fn.params):
                raise RuntimeError_(
                    f"函数 {node.name} 需要 {len(fn.params)} 个参数，"
                    f"但传入了 {len(node.args)} 个",
                    node.line,
                )
            arg_values = [self._eval(arg) for arg in node.args]
            callee = self._push_frame(node.name, node.line)
            for param, value in zip(fn.params, arg_values):
                callee.locals[param] = value
            self._emit({"kind": "call", "line": node.line, "name": node.name})
            try:
                self._exec_stmts(fn.body)
            except _ReturnSignal as signal:
                result = signal.value
            else:
                result = 0
            self.frames.pop()
            self._emit({"kind": "return", "line": node.line, "name": node.name})
            return result
        raise RuntimeError_("未知表达式", getattr(node, "line", 1))

    def _binary(self, op: str, left, right, line: int) -> object:
        if op in ("+", "-", "*", "/", "%", "<", ">", "<=", ">="):
            _expect_int(left, line)
            _expect_int(right, line)
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                if right == 0:
                    raise RuntimeError_("除以零", line)
                q = abs(left) // abs(right)
                return q if (left < 0) == (right < 0) else -q
            if op == "%":
                if right == 0:
                    raise RuntimeError_("对零取模", line)
                return left - (self._binary("/", left, right, line)) * right
            if op == "<":
                return left < right
            if op == ">":
                return left > right
            if op == "<=":
                return left <= right
            return left >= right
        if op in ("==", "!="):
            equal = left == right and type(left) is type(right)
            return equal if op == "==" else not equal
        raise RuntimeError_(f"不支持的运算符 {op}", line)


def _expect_int(value, line: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError_("此处需要整数", line)


def _is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return False


def _display(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
