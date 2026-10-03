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
COMPARISONS_DIR = Path(__file__).resolve().parent / "comparisons"


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

    def __init__(
        self,
        source: str,
        session_id: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        fork_cursor: int | None = None,
        bp_migrations: list[dict] | None = None,
    ):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.created_at = time.time()
        self.name = name or ("主分支" if parent_id is None else "实验分支")
        self.parent_id = parent_id
        # Cursor position in the parent session this branch was forked from.
        self.fork_cursor = fork_cursor
        # Human-confirmed breakpoint migrations that produced this branch's
        # breakpoints: [{"oldLine": n, "newLine": m, "fromHash": ..., "toHash": ...}].
        self.bp_migrations: list[dict] = list(bp_migrations or [])
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

    # ---- branches ----

    def fork(
        self,
        source: str | None = None,
        name: str | None = None,
        breakpoints: list[int] | None = None,
        bp_migrations: list[dict] | None = None,
    ) -> "DebugSession":
        """Derive an independent experiment branch from the current pause.

        The parent session is never mutated. With an unchanged program the
        branch inherits the exact pause cursor, breakpoints and trace, so
        both sides continue from the same spot independently. With an
        edited program the candidate is compiled first (SourceError leaves
        every existing branch untouched) and the branch starts from the
        top with the explicitly confirmed breakpoints; the confirmed
        old->new line mapping is recorded so it survives save/reopen.
        """
        new_source = self.source if source is None else source
        if source_hash(new_source) == self.source_hash:
            branch = DebugSession(
                self.source,
                name=name,
                parent_id=self.id,
                fork_cursor=self.cursor,
                bp_migrations=bp_migrations,
            )
            branch.breakpoints = set(self.breakpoints)
            branch.cursor = self.cursor
            return branch
        # Compile fully before the branch object exists; a failed edit
        # therefore cannot affect this session or any sibling branch.
        DebugSession._build_trace(new_source)
        branch = DebugSession(
            new_source,
            name=name,
            parent_id=self.id,
            fork_cursor=self.cursor,
            bp_migrations=bp_migrations,
        )
        branch.breakpoints = {
            ln for ln in (breakpoints or []) if isinstance(ln, int) and ln > 0
        }
        return branch

    # ---- comparison ----

    @staticmethod
    def _event_fact(event: dict) -> dict:
        """Execution facts of one event used for branch comparison.

        Line numbers are deliberately excluded: across program revisions
        the same line number is not the same step. Facts are the event
        kind, the output produced so far, and each frame's variables.
        """
        return {
            "kind": event["kind"],
            "output": event.get("output", ""),
            "frames": [
                (
                    frame["name"],
                    tuple(
                        (var["name"], var["value"]["display"])
                        for var in frame["locals"]
                    ),
                )
                for frame in event.get("stack", [])
            ],
        }

    def pause_boundaries(self) -> list[dict]:
        """Saved pause boundaries the user may compare at.

        Only positions already reached (index <= cursor) are offered;
        future events are never presented as selectable facts.
        """
        boundaries = []
        for event in self.events[: self.cursor + 1]:
            if event["kind"] == "line":
                boundaries.append({
                    "index": event["index"],
                    "line": event.get("line"),
                    "kind": event["kind"],
                })
        return boundaries

    def _executed_events(self, boundary: int) -> list[dict]:
        """Events that have actually happened up to ``boundary`` (inclusive)."""
        if boundary < 0:
            return []
        return self.events[: min(boundary, len(self.events) - 1) + 1]

    def _executed_steps(self, boundary: int) -> list[dict]:
        """User-visible pause steps that actually happened up to ``boundary``.

        The steps a user can pause at are the ``line`` events; internal
        events such as ``stmt_end``/``call``/``return`` are bookkeeping
        between pauses and must not be aligned by raw event index, since
        inserted lines or different call depth would then make the same
        logical step look misaligned. Each step carries the cumulative
        output and full stack snapshot observed at that pause. A terminal
        ``error``/``end`` event is appended as the final step so program
        exit and failure remain comparable facts.
        """
        steps = []
        for event in self._executed_events(boundary):
            if event["kind"] == "line":
                steps.append(event)
        if boundary >= len(self.events) - 1:
            last = self.events[-1]
            if last["kind"] in ("error", "end"):
                steps.append(last)
        return steps

    def compare(
        self,
        other: "DebugSession",
        left_boundary: int | None = None,
        right_boundary: int | None = None,
    ) -> dict:
        """Compare two branches at independently chosen saved pause boundaries.

        Defaults to each side's current cursor, so branches never have to
        advance in lockstep. Events beyond a boundary have not run on that
        side and are reported as "waiting", never as divergence; a side
        that simply executed more is reported as "ahead". Line numbers
        are not part of the compared facts (the same line number across
        revisions is a different event); each reported event carries its
        revision hash and full stack snapshot for tracing back to
        variables, call site and program revision.
        """
        left_idx = self.cursor if left_boundary is None else left_boundary
        right_idx = other.cursor if right_boundary is None else right_boundary
        if not -1 <= left_idx <= self.cursor:
            raise ValueError("左侧边界尚未执行，不能比较未来事件")
        if not -1 <= right_idx <= other.cursor:
            raise ValueError("右侧边界尚未执行，不能比较未来事件")

        left_events = self._executed_steps(left_idx)
        right_events = other._executed_steps(right_idx)

        result = {
            "status": "identical",
            "diverged": False,
            "index": None,
            "reason": None,
            "common": 0,
            "sameRevision": self.source_hash == other.source_hash,
            "left": {
                "id": self.id,
                "name": self.name,
                "sourceHash": self.source_hash,
                "boundary": left_idx,
                "cursor": self.cursor,
                "executedCount": len(self._executed_events(left_idx)),
                "stepCount": len(left_events),
                "terminal": bool(left_events) and left_events[-1]["kind"] in (
                    "error", "end"
                ),
                "event": None,
            },
            "right": {
                "id": other.id,
                "name": other.name,
                "sourceHash": other.source_hash,
                "boundary": right_idx,
                "cursor": other.cursor,
                "executedCount": len(other._executed_events(right_idx)),
                "stepCount": len(right_events),
                "terminal": bool(right_events) and right_events[-1]["kind"] in (
                    "error", "end"
                ),
                "event": None,
            },
        }

        def brief(session, event):
            if event is None:
                return None
            return {
                "index": event["index"],
                "kind": event["kind"],
                "line": event.get("line"),
                "sourceHash": session.source_hash,
                "output": event.get("output", ""),
                "stack": event.get("stack", []),
                "message": event.get("message"),
            }

        def first_line_event(events, start):
            """Next real pause point at/after ``start`` on the longer side."""
            for event in events[start:]:
                if event["kind"] == "line":
                    return event
            return events[start]

        # Only the common executed prefix can be compared fact-to-fact.
        common = min(len(left_events), len(right_events))
        for i in range(common):
            left_fact = self._event_fact(left_events[i])
            right_fact = self._event_fact(right_events[i])
            if left_fact == right_fact:
                continue
            if left_fact["kind"] != right_fact["kind"]:
                reason = "kind"
            elif left_fact["output"] != right_fact["output"]:
                reason = "output"
            else:
                reason = "variables"
            result.update({
                "status": "diverged",
                "diverged": True,
                "index": i,
                "reason": reason,
                "common": i,
            })
            result["left"]["event"] = brief(self, left_events[i])
            result["right"]["event"] = brief(other, right_events[i])
            return result
        # The common executed prefix matches. If one side simply executed
        # more events (more loop iterations, deeper recursion, or just
        # further along), that is "ahead", not a factual difference.
        if len(left_events) != len(right_events):
            if len(left_events) > len(right_events):
                longer_side, longer_session, longer_events = (
                    result["left"], self, left_events
                )
            else:
                longer_side, longer_session, longer_events = (
                    result["right"], other, right_events
                )
            result.update({
                "status": "ahead",
                "reason": "ahead",
                "index": common,
                "common": common,
            })
            representative = first_line_event(longer_events, common)
            longer_side["event"] = brief(
                longer_session, representative
            )
            return result

        # Same number of executed events and equal facts, but at least one
        # side has not reached its end: equality is provisional only, so
        # report "waiting" instead of pretending the branches agree.
        if not (
            result["left"]["terminal"] and result["right"]["terminal"]
        ):
            result["status"] = "waiting"
        result["common"] = common
        return result

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
            "name": self.name,
            "cursor": self.cursor,
            "parentId": self.parent_id,
            "forkCursor": self.fork_cursor,
            "bpMigrations": self.bp_migrations,
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
            "name": self.name,
            "parent_id": self.parent_id,
            "fork_cursor": self.fork_cursor,
            "bp_migrations": self.bp_migrations,
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
        session = cls(
            record["source"],
            session_id=record["id"],
            name=record.get("name"),
            parent_id=record.get("parent_id"),
            fork_cursor=record.get("fork_cursor"),
            bp_migrations=record.get("bp_migrations"),
        )
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
                "name": record.get("name"),
                "cursor": record.get("cursor", -1),
                "parentId": record.get("parent_id"),
                "sourceHash": source_hash(record.get("source", "")),
                "preview": record.get("source", "").strip().splitlines()[:1],
            })
        return saved


class ComparisonStore:
    """Persists saved branch comparisons as self-contained work results.

    Each record pins both sides' program revision hashes, the chosen pause
    boundaries, the cursors at save time and the full comparison outcome
    (first divergence, call stacks, variable values, outputs). Later
    stepping, continuing, restarting or even deleting a branch never
    rewrites a saved record, so it can be reopened and audited as-is.
    """

    def __init__(self, directory: Path = COMPARISONS_DIR):
        self.directory = directory

    def _path(self, comparison_id: str) -> Path:
        return self.directory / f"{comparison_id}.json"

    def save(
        self,
        left: DebugSession,
        right: DebugSession,
        left_boundary: int | None = None,
        right_boundary: int | None = None,
        name: str | None = None,
    ) -> dict:
        # Compute once, then freeze: the stored result is a snapshot and
        # is never recomputed against the live branches afterwards.
        result = left.compare(
            right, left_boundary=left_boundary, right_boundary=right_boundary
        )
        record = {
            "id": uuid.uuid4().hex[:12],
            "created_at": time.time(),
            "name": name or f"{left.name} vs {right.name}",
            "left_id": left.id,
            "right_id": right.id,
            "left_boundary": result["left"]["boundary"],
            "right_boundary": result["right"]["boundary"],
            "result": result,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(record["id"]).write_text(
            json.dumps(record, ensure_ascii=False, indent=2)
        )
        return record

    @staticmethod
    def _summary(record: dict) -> dict:
        result = record.get("result", {})
        return {
            "id": record["id"],
            "created_at": record.get("created_at"),
            "name": record.get("name"),
            "leftId": record.get("left_id"),
            "rightId": record.get("right_id"),
            "leftBoundary": record.get("left_boundary"),
            "rightBoundary": record.get("right_boundary"),
            "status": result.get("status"),
            "leftName": result.get("left", {}).get("name"),
            "rightName": result.get("right", {}).get("name"),
            "leftHash": result.get("left", {}).get("sourceHash"),
            "rightHash": result.get("right", {}).get("sourceHash"),
        }

    def list(self) -> list[dict]:
        if not self.directory.exists():
            return []
        saved = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                record = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            saved.append(self._summary(record))
        saved.sort(key=lambda item: item.get("created_at") or 0)
        return saved

    def get(self, comparison_id: str) -> dict:
        return json.loads(self._path(comparison_id).read_text())

    def delete(self, comparison_id: str) -> None:
        path = self._path(comparison_id)
        if path.exists():
            path.unlink()


class SessionStore:


    """Keeps live sessions in memory and persists them on every mutation."""

    def __init__(self, directory: Path = SESSIONS_DIR):
        self.directory = directory
        self.sessions: dict[str, DebugSession] = {}
        self.comparisons = ComparisonStore()

    def create(self, source: str) -> DebugSession:
        session = DebugSession(source)
        self.sessions[session.id] = session
        session.save(self.directory)
        return session

    def register(self, session: DebugSession) -> DebugSession:
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
