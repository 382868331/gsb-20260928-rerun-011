from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum

from ..minilang import Lexer, Parser, RuntimeEngine
from ..minilang.lexer import LexError
from ..minilang.parser import ParseError
from ..minilang.runtime import RuntimeError_, TraceLimitError

LINE_EVENT = "line"
TERMINAL_KINDS = ("error", "end")


class StepKind(str, Enum):
    INTO = "into"
    OVER = "over"
    OUT = "out"


class SessionError(Exception):
    def __init__(self, message: str, line: int | None = None, code: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.line = line
        self.code = code


def compile_source(source: str):
    try:
        tokens = Lexer().tokenize(source)
        program = Parser(tokens).parse()
        engine = RuntimeEngine(program)
        events = engine.run()
    except (LexError, ParseError) as exc:
        line = getattr(exc, "line", None)
        raise SessionError(str(exc), line=line, code="compile_error") from exc
    except TraceLimitError as exc:
        raise SessionError(exc.message, line=exc.line, code="trace_limit") from exc
    except RuntimeError_ as exc:
        raise SessionError(exc.message, line=exc.line, code="runtime_error") from exc
    return program, events


def executable_lines(events: list[dict]) -> list[int]:
    return sorted({e["line"] for e in events if e["kind"] == LINE_EVENT})


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class DebugSession:
    """A cursor over one program's immutable trace plus mutable breakpoints."""

    id: str
    name: str
    source: str
    events: list[dict]
    breakpoints: set[int] = field(default_factory=set)
    rejected_breakpoints: dict[int, str] = field(default_factory=dict)
    cursor: int = -1
    status: str = 'paused'  # paused | ended | error | closed
    source_hash: str = ''

    def __post_init__(self):
        if not self.source_hash:
            self.source_hash = source_hash(self.source)
        if self.cursor < 0:
            self.cursor = self._first_pause(0)
            self.status = self._status_for_cursor(self.cursor)

    @classmethod
    def create(cls, source: str, name: str = '未命名会话',
               breakpoints: set[int] | None = None,
               session_id: str | None = None) -> 'DebugSession':
        _, events = compile_source(source)
        session = cls(
            id=session_id or new_session_id(),
            name=name,
            source=source,
            events=events,
        )
        if breakpoints:
            session.set_breakpoints(set(breakpoints))
        return session

    @property
    def executable_line_set(self) -> set[int]:
        return {e['line'] for e in self.events if e['kind'] == LINE_EVENT}

    @property
    def executable_line_list(self) -> list[int]:
        return executable_lines(self.events)

    @property
    def current_event(self) -> dict | None:
        if 0 <= self.cursor < len(self.events):
            return self.events[self.cursor]
        return None

    @property
    def is_terminal(self) -> bool:
        event = self.current_event
        return event is not None and event['kind'] in TERMINAL_KINDS

    def state(self) -> dict:
        event = self.current_event
        error = None
        if event is not None and event['kind'] == 'error':
            error = {
                'message': event['message'],
                'line': event['line'],
                'stack': event.get('stack', []),
                'output': event.get('output', ''),
            }
        return {
            'id': self.id,
            'name': self.name,
            'source': self.source,
            'source_hash': self.source_hash,
            'status': self.status,
            'breakpoints': sorted(self.breakpoints),
            'rejected_breakpoints': [
                {'line': line, 'reason': reason}
                for line, reason in sorted(self.rejected_breakpoints.items())
            ],
            'executable_lines': self.executable_line_list,
            'cursor': self.cursor,
            'event': event,
            'error': error,
            'event_count': len(self.events),
        }

    def set_breakpoints(self, lines) -> dict:
        # Only lines carrying an executable statement are accepted; other
        # line numbers are rejected instead of silently remapped.
        wanted = {int(line) for line in lines}
        valid = self.executable_line_set
        accepted = {line for line in wanted if line in valid}
        rejected = {
            line: '该行没有可执行语句，断点未生效'
            for line in wanted
            if line not in valid
        }
        self.breakpoints = accepted
        self.rejected_breakpoints = rejected
        return {'accepted': sorted(accepted), 'rejected': rejected}

    def step(self, kind: StepKind | str) -> dict:
        self._require_alive()
        kind = StepKind(kind)
        event = self.current_event
        if event is None or event['kind'] != LINE_EVENT:
            raise SessionError('程序已结束，无法继续单步执行', code='finished')
        if kind is StepKind.INTO:
            target = self._next_line(self.cursor + 1, lambda e: True)
        elif kind is StepKind.OUT:
            depth = len(event['stack'])
            target = self._next_line(
                self.cursor + 1, lambda e: len(e['stack']) < depth
            )
        else:
            target = self._step_over_target()
        self.cursor = self._first_pause(target)
        self._refresh_status()
        return self.state()

    def resume(self) -> dict:
        # Continue until the next breakpoint (or program termination).
        self._require_alive()
        event = self.current_event
        if event is None or event['kind'] != LINE_EVENT:
            raise SessionError('程序已结束，无法继续执行', code='finished')
        target = self._scan(
            self.cursor + 1,
            lambda e: e['kind'] == LINE_EVENT and e['line'] in self.breakpoints,
        )
        self.cursor = target if target is not None else len(self.events) - 1
        self._refresh_status()
        return self.state()

    def close(self) -> None:
        self.status = 'closed'

    def _require_alive(self) -> None:
        if self.status == 'closed':
            raise SessionError('该调试会话已结束，请重新开始调试', code='closed')

    def _refresh_status(self) -> None:
        self.status = self._status_for_cursor(self.cursor)

    def _status_for_cursor(self, index: int) -> str:
        event = self.events[index]
        if event['kind'] == 'error':
            return 'error'
        if event['kind'] == 'end':
            return 'ended'
        return 'paused'

    def _scan(self, start: int, predicate) -> int | None:
        for index in range(max(start, 0), len(self.events)):
            if predicate(self.events[index]):
                return index
        return None

    def _next_line(self, start: int, predicate) -> int:
        target = self._scan(
            start,
            lambda e: e['kind'] == LINE_EVENT and predicate(e),
        )
        return target if target is not None else len(self.events) - 1

    def _first_pause(self, start: int) -> int:
        index = self._scan(start, lambda e: e['kind'] == LINE_EVENT)
        return index if index is not None else len(self.events) - 1

    def _step_over_target(self) -> int:
        level = self.current_event['level']
        end_index = None
        for index in range(self.cursor + 1, len(self.events)):
            event = self.events[index]
            if event['kind'] == 'stmt_end' and event.get('level') == level:
                end_index = index
                break
        if end_index is None:
            return len(self.events) - 1
        for index in range(end_index + 1, len(self.events)):
            event = self.events[index]
            if event['kind'] == LINE_EVENT and event['level'] <= level:
                return index
        return len(self.events) - 1
