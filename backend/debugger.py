from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from .minilang import Lexer, Parser, RuntimeEngine
from .minilang.lexer import LexError
from .minilang.parser import ParseError

SESSIONS_DIR = Path(__file__).resolve().parent / "sessions"


class SourceError(Exception):
    """Raised when the source program fails to lex or parse."""


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def compile_source(source: str):
    try:
        tokens = Lexer().tokenize(source)
        return Parser(tokens).parse()
    except (LexError, ParseError) as exc:
        raise SourceError(str(exc)) from exc


def _executable_lines(events: list[dict]) -> set[int]:
    """Lines that can host a pause in a recorded trace (1-based)."""
    return {evt["line"] for evt in events if evt["kind"] == "line"}


def _line_text(lines: list[str], line_number: int) -> str:
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()
    return ""


def _normalize(text: str) -> str:
    return " ".join(text.split())


class DebugSession:
    """A replay-based debug session over the recorded event trace.

    The trace is deterministic for a given source, so pausing, stepping
    and continuing are pure cursor movements; every paused state carries
    a full per-frame stack snapshot, which keeps recursion locals bound
    to their own frame and makes post-exit inspection trivial.
    """

    def __init__(self, source: str, session_id: str | None = None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.created_at = time.time()
        self.breakpoints: set[int] = set()
        self.cursor = -1
        _, self.events = self._build_trace(source)
        self.source = source
        self.source_hash = source_hash(source)

    # ---- source / trace ----

    @staticmethod
    def _build_trace(source: str):
        """Compile and run ``source`` without touching any session state.

        Raises ``SourceError`` if the program cannot be parsed, so callers
        can reject an edit before the live session is modified in any way.
        """
        program = compile_source(source)
        events = RuntimeEngine(program).run()
        return program, events

    def preview_source_change(self, source: str) -> dict:
        """Preview a source switch without mutating the session.

        The candidate program is compiled in full first; a lex/parse
        failure raises ``SourceError`` and leaves breakpoints, cursor and
        stack snapshots exactly as they were. When compilation succeeds,
        each old breakpoint is mapped onto the new line numbering and
        classified as directly reusable or as needing reconfirmation.
        """
        if source_hash(source) == self.source_hash:
            return {"changed": False, "migrations": []}
        _, new_events = self._build_trace(source)

        old_exec = _executable_lines(self.events)
        new_exec = _executable_lines(new_events)
        old_texts = self.source.splitlines()
        new_texts = source.splitlines()

        migrations = []
        for old_line in sorted(self.breakpoints):
            entry = {
                "oldLine": old_line,
                "oldText": _line_text(old_texts, old_line),
                "status": "confirm",
                "newLine": None,
                "candidates": [],
            }
            if old_line in old_exec:
                target = _normalize(entry["oldText"])
                matches = [
                    ln
                    for ln in new_exec
                    if _normalize(_line_text(new_texts, ln)) == target
                ]
                entry["candidates"] = [
                    {"line": ln, "text": _line_text(new_texts, ln)}
                    for ln in matches
                ]
                if len(matches) == 1:
                    entry["status"] = "reusable"
                    entry["newLine"] = matches[0]
            migrations.append(entry)

        return {
            "changed": True,
            "source": source,
            "newSourceHash": source_hash(source),
            "executableLines": sorted(new_exec),
            "migrations": migrations,
        }

    def commit_source_change(self, source: str, breakpoints: list[int]) -> dict:
        """Atomically switch to an already validated new program.

        Compilation happens before any field is touched, so even a
        last-moment failure keeps the old session intact. On success the
        old trace/cursor/stack are replaced together with the source and
        the explicitly confirmed breakpoints; the new session always
        starts before its first line, never on an old frame.
        """
        if source_hash(source) == self.source_hash:
            return {"changed": False}
        # Build the whole new trace first; this raises before mutation.
        _, events = self._build_trace(source)
        self.events = events
        self.source = source
        self.source_hash = source_hash(source)
        self.cursor = -1
        self.breakpoints = {
            ln for ln in breakpoints if isinstance(ln, int) and ln > 0
        }
        return {"changed": True}

    # ---- breakpoints ----

    def set_breakpoints(self, lines: list[int]) -> None:
        self.breakpoints = {ln for ln in lines if isinstance(ln, int) and ln > 0}

    # ---- stepping ----

    def _line_events_after(self, start: int):
        for i in range(start + 1, len(self.events)):
            if self.events[i]["kind"] == "line":
                yield i

    def _finish(self) -> None:
        self.cursor = len(self.events) - 1

    def step_into(self) -> None:
        if self.status() != "paused":
            return
        for i in self._line_events_after(self.cursor):
            self.cursor = i
            return
        self._finish()

    def step_over(self) -> None:
        if self.status() != "paused":
            return
        level = self.events[self.cursor].get("level", 1) if self.cursor >= 0 else 0
        for i in self._line_events_after(self.cursor):
            if self.events[i].get("level", 1) <= level:
                self.cursor = i
                return
        self._finish()

    def continue_(self) -> None:
        if self.status() != "paused":
            return
        for i in self._line_events_after(self.cursor):
            if self.events[i]["line"] in self.breakpoints:
                self.cursor = i
                return
        self._finish()

    def restart(self) -> None:
        self.cursor = -1

    # ---- state ----

    def status(self) -> str:
        if self.cursor < len(self.events) - 1:
            return "paused"
        last = self.events[-1]
        return "error" if last["kind"] == "error" else "finished"

    def current_event(self) -> dict:
        if self.cursor < 0:
            return {
                "index": -1,
                "kind": "start",
                "line": None,
                "output": "",
                "stack": [],
            }
        return self.events[self.cursor]

    def state(self) -> dict:
        event = self.current_event()
        status = self.status()
        state = {
            "id": self.id,
            "status": status,
            "line": event.get("line"),
            "kind": event["kind"],
            "output": event.get("output", ""),
            "stack": event.get("stack", []),
            "breakpoints": sorted(self.breakpoints),
            "source": self.source,
            "sourceHash": self.source_hash,
            "error": None,
        }
        if status == "error":
            state["error"] = {
                "line": event.get("line"),
                "message": event.get("message", ""),
                "stack": event.get("stack", []),
            }
        return state

    # ---- persistence ----

    def to_record(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "source": self.source,
            "breakpoints": sorted(self.breakpoints),
            "cursor": self.cursor,
        }

    def save(self, directory: Path = SESSIONS_DIR) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.json"
        path.write_text(json.dumps(self.to_record(), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, session_id: str, directory: Path = SESSIONS_DIR) -> "DebugSession":
        path = directory / f"{session_id}.json"
        record = json.loads(path.read_text())
        session = cls(record["source"], session_id=record["id"])
        session.created_at = record.get("created_at", session.created_at)
        # Breakpoints are restored only because the source (and therefore
        # its line numbering) is identical to what they were set on.
        session.breakpoints = set(record.get("breakpoints", []))
        cursor = record.get("cursor", -1)
        session.cursor = max(-1, min(cursor, len(session.events) - 1))
        return session

    @staticmethod
    def list_saved(directory: Path = SESSIONS_DIR) -> list[dict]:
        if not directory.exists():
            return []
        saved = []
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            saved.append({
                "id": record["id"],
                "created_at": record.get("created_at"),
                "sourceHash": source_hash(record.get("source", "")),
                "preview": record.get("source", "").strip().splitlines()[:1],
            })
        return saved


class SessionStore:
    """Keeps live sessions in memory and persists them on every mutation."""

    def __init__(self, directory: Path = SESSIONS_DIR):
        self.directory = directory
        self.sessions: dict[str, DebugSession] = {}

    def create(self, source: str) -> DebugSession:
        session = DebugSession(source)
        self.sessions[session.id] = session
        session.save(self.directory)
        return session

    def get(self, session_id: str) -> DebugSession:
        if session_id not in self.sessions:
            self.sessions[session_id] = DebugSession.load(session_id, self.directory)
        return self.sessions[session_id]

    def touch(self, session: DebugSession) -> None:
        session.save(self.directory)

    def delete(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        path = self.directory / f"{session_id}.json"
        if path.exists():
            path.unlink()

    def list_saved(self) -> list[dict]:
        return DebugSession.list_saved(self.directory)
