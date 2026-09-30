from __future__ import annotations

import hashlib
import json
import time
import uuid
from difflib import SequenceMatcher
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
        self._load_source(source)

    # ---- source / trace ----

    def _load_source(self, source: str) -> None:
        program = compile_source(source)
        engine = RuntimeEngine(program)
        self.events = engine.run()
        self.source = source
        self.source_hash = source_hash(source)
        self.cursor = -1

    def preview_source_update(self, source: str) -> dict:
        """Validate and preview a source update without mutating the session."""
        if source_hash(source) == self.source_hash:
            return {
                "changed": False,
                "source": source,
                "source_hash": self.source_hash,
                "retained_breakpoints": [],
                "reconfirm_breakpoints": [],
            }

        # Compilation is the acceptance gate. Doing this before touching any
        # session field means a lex/parse failure leaves the old trace, cursor
        # and breakpoints intact.
        program = compile_source(source)
        candidate_events = RuntimeEngine(program).run()
        executable_lines = {
            event["line"]
            for event in candidate_events
            if event["kind"] == "line" and event.get("line") is not None
        }
        old_lines = self.source.splitlines()
        new_lines = source.splitlines()
        equal_map = {}
        matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if tag != "equal":
                continue
            for offset in range(old_end - old_start):
                equal_map[old_start + offset + 1] = new_start + offset + 1
        new_occurrences: dict[str, list[int]] = {}
        for index, line in enumerate(new_lines, start=1):
            text = line.strip()
            if text:
                new_occurrences.setdefault(text, []).append(index)

        retained = []
        reconfirm = []
        for old_line in sorted(self.breakpoints):
            text = old_lines[old_line - 1].strip() if 0 < old_line <= len(old_lines) else ""
            aligned_line = equal_map.get(old_line)
            matches = new_occurrences.get(text, []) if text else []
            executable_matches = [line for line in matches if line in executable_lines]
            if aligned_line in executable_lines:
                target_line = aligned_line
            elif len(executable_matches) == 1:
                target_line = executable_matches[0]
            else:
                target_line = None
            if target_line is not None:
                retained.append({
                    "from": old_line,
                    "to": target_line,
                    "text": text,
                })
            else:
                reconfirm.append({
                    "from": old_line,
                    "text": text,
                    "candidates": self._nearby_candidates(
                        old_line, len(new_lines), executable_lines
                    ),
                })

        return {
            "changed": True,
            "source": source,
            "source_hash": source_hash(source),
            "retained_breakpoints": retained,
            "reconfirm_breakpoints": reconfirm,
        }

    @staticmethod
    def _nearby_candidates(
        old_line: int, new_line_count: int, executable_lines: set[int]
    ) -> list[int]:
        if not executable_lines:
            return []
        target = max(1, min(old_line, new_line_count))
        return sorted(executable_lines, key=lambda line: (abs(line - target), line))[:3]

    def commit_source_update(self, source: str, breakpoints: list[int] | None = None) -> dict:
        """Atomically accept a validated program and its chosen breakpoints."""
        preview = self.preview_source_update(source)
        if not preview["changed"]:
            return {
                "changed": False,
                "invalidated_breakpoints": [],
                "migrated_breakpoints": [],
                "reconfirm_breakpoints": [],
            }

        if breakpoints is None:
            selected_lines = {item["to"] for item in preview["retained_breakpoints"]}
        else:
            selected_lines = {
                line for line in breakpoints if isinstance(line, int) and line > 0
            }

        retained = preview["retained_breakpoints"]
        chosen_reconfirm = []
        invalidated = []
        for item in preview["reconfirm_breakpoints"]:
            chosen_line = next(
                (line for line in item["candidates"] if line in selected_lines), None
            )
            if chosen_line is None:
                invalidated.append(item["from"])
            else:
                chosen_reconfirm.append({
                    "from": item["from"],
                    "to": chosen_line,
                    "text": item["text"],
                })

        # Keep the previous trace in locals until the new trace is ready. If
        # compilation unexpectedly fails, assignment never starts.
        program = compile_source(source)
        engine = RuntimeEngine(program)
        events = engine.run()
        self.events = events
        self.source = source
        self.source_hash = preview["source_hash"]
        self.cursor = -1
        self.breakpoints = selected_lines

        return {
            "changed": True,
            "invalidated_breakpoints": invalidated,
            "migrated_breakpoints": retained + chosen_reconfirm,
            "reconfirm_breakpoints": invalidated,
        }

    def update_source(self, source: str) -> dict:
        """Backward-compatible wrapper used by the immediate-commit API."""
        return self.commit_source_update(source)

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
