"""Debug session controller built on top of the event-generating interpreter."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field

from .interpreter import Frame, Interpreter
from .parser import parse_program

STEP_LIMIT = 1_000_000  # events consumed by a single continue/step request


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


@dataclass
class PauseSnapshot:
    reason: str
    line: int
    frames: list[dict]
    seq: int


@dataclass
class Session:
    session_id: str
    source: str
    program: object
    breakpoints: set[int] = field(default_factory=set)
    events: list[dict] = field(default_factory=list)
    output: list[dict] = field(default_factory=list)
    status: str = "created"  # created | paused | finished | error
    error: dict | None = None
    current_line: int | None = None
    snapshot: list[dict] = field(default_factory=list)
    interpreter: Interpreter | None = None
    _gen = None

    @property
    def src_hash(self) -> str:
        return source_hash(self.source)

    def breakable_lines(self) -> list[int]:
        lines: set[int] = set()

        def walk(statements) -> None:
            for stmt in statements:
                lines.add(stmt.line)
                body_lists = []
                if hasattr(stmt, "body"):
                    body_lists.append(stmt.body)
                if hasattr(stmt, "else_body") and stmt.else_body is not None:
                    body_lists.append(stmt.else_body)
                for body in body_lists:
                    walk(body)

        for fn in self.program.functions.values():
            walk(fn.body)
        return sorted(lines)

    def start(self) -> None:
        if self.status != "created":
            return
        self.interpreter = Interpreter(self.program)
        self._gen = self.interpreter.run()
        self._run_until_pause("start", base_depth=0)

    def step_into(self) -> None:
        self._resume(lambda depth, event, base_depth: event["type"] == "line")

    def step_over(self) -> None:
        base_depth = len(self.snapshot)
        self._resume(
            lambda depth, event, base_depth=base_depth: event["type"] == "line" and depth <= base_depth
        )

    def continue_run(self) -> None:
        self._resume(lambda depth, event, base_depth: self._is_breakpoint_hit(event))

    def set_breakpoint(self, line: int) -> dict:
        if not isinstance(line, int) or line < 1:
            return {"ok": False, "error": "line must be a positive integer"}
        if line not in self.breakable_lines():
            return {"ok": False, "error": f"line {line} does not contain a statement that can break there"}
        self.breakpoints.add(line)
        return {"ok": True, "breakpoints": sorted(self.breakpoints)}

    def clear_breakpoint(self, line: int) -> dict:
        self.breakpoints.discard(line)
        return {"ok": True, "breakpoints": sorted(self.breakpoints)}

    def _is_breakpoint_hit(self, event: dict) -> bool:
        return event["type"] == "line" and event["line"] in self.breakpoints

    def _resume(self, should_pause) -> None:
        if self.status != "paused":
            return
        self.status = "running"
        base_depth = len(self.snapshot)
        self._run_until_pause("step", base_depth, should_pause)

    def _run_until_pause(self, reason: str, base_depth: int, should_pause=None) -> None:
        steps = 0
        try:
            for event in self._gen:
                steps += 1
                self._record(event)
                if event["type"] == "error":
                    self.status = "error"
                    self.error = {"line": event["line"], "message": event["message"]}
                    self.snapshot = self._frame_snapshot()
                    self.current_line = event["line"]
                    return
                if event["type"] == "exit":
                    self.status = "finished"
                    self.current_line = None
                    self.snapshot = []
                    return
                if event["type"] == "line":
                    self.current_line = event["line"]
                    hit = False
                    if reason == "start":
                        hit = True
                    elif should_pause is not None:
                        hit = should_pause(len(self.interpreter.frames), event, base_depth)
                    if hit:
                        self.status = "paused"
                        self.snapshot = self._frame_snapshot()
                        self._last_pause = PauseSnapshot(reason, event["line"], copy.deepcopy(self.snapshot), len(self.events))
                        return
                if steps >= STEP_LIMIT:
                    self.status = "error"
                    self.error = {"line": self.current_line or 0, "message": "execution step limit reached (possible infinite loop)"}
                    self.snapshot = self._frame_snapshot()
                    return
        except Exception as exc:  # defensive: interpreter bugs surface as runtime errors
            self.status = "error"
            self.error = {"line": self.current_line or 0, "message": f"internal error: {exc}"}
            self.snapshot = self._frame_snapshot()

    def _record(self, event: dict) -> None:
        record = dict(event)
        record["seq"] = len(self.events)
        self.events.append(record)
        if event["type"] == "output":
            self.output.append({"seq": record["seq"], "line": event["line"], "text": event["text"]})

    def _frame_snapshot(self) -> list[dict]:
        if self.interpreter is None:
            return []
        result = []
        for frame in self.interpreter.frames:
            assert isinstance(frame, Frame)
            result.append(
                {
                    "name": frame.name,
                    "line": frame.line,
                    "locals": copy.deepcopy(frame.locals),
                }
            )
        return result

    def variables(self) -> dict:
        # Reading variables only reads the last pause snapshot and never advances
        # the suspended generator.
        return {"status": self.status, "frames": copy.deepcopy(self.snapshot)}

    def state(self) -> dict:
        return {
            "sessionId": self.session_id,
            "status": self.status,
            "currentLine": self.current_line,
            "breakpoints": sorted(self.breakpoints),
            "breakableLines": self.breakable_lines(),
            "srcHash": self.src_hash,
            "frames": copy.deepcopy(self.snapshot),
            "output": list(self.output),
            "error": self.error,
            "eventCount": len(self.events),
        }


def create_session(session_id: str, source: str) -> Session:
    program = parse_program(source)
    session = Session(session_id=session_id, source=source, program=program)
    return session
