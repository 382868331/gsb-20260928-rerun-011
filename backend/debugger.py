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

    def fork_at(self, index: int, name: str | None = None) -> "DebugSession":
        """Derive an investigation branch paused exactly at the given index.

        Used to continue investigating from a divergence location (or its
        surrounding call sites). The derivation is pure: this session's
        cursor, trace, breakpoints and persisted record are all left
        untouched, and the new branch simply replays the same
        deterministic trace up to the requested event.
        """
        if not -1 <= index <= len(self.events) - 1:
            raise ValueError("派生位置超出已记录的执行范围")
        branch = DebugSession(
            self.source,
            name=name or "调查分支",
            parent_id=self.id,
            fork_cursor=index,
        )
        branch.breakpoints = set(self.breakpoints)
        branch.cursor = index
        return branch

    # ---- divergence localization ----

    def _step_key(self, event: dict, lines: list[str]):
        """Semantic correspondence key of one explored step.

        A pause step corresponds across revisions only when it is the
        same normalized statement text executed under the same call path
        (function names from outermost to innermost frame). Line numbers
        and raw step counts are deliberately excluded: inserted lines,
        changed call depth, extra loop iterations or deeper recursion
        must not make unrelated positions look aligned. Terminal events
        correspond only to the same terminal kind, so an early error on
        one side never matches a normal exit on the other.
        """
        if event["kind"] == "line":
            path = tuple(frame["name"] for frame in event.get("stack", []))
            text = _normalize(_line_text(lines, event.get("line") or 0))
            return ("line", path, text)
        return ("terminal", event["kind"])

    def _explored_steps(self, start: int, budget: int | None):
        """Pause steps after the given cursor, capped at the budget.

        Exploration is pure replay over the recorded deterministic
        trace; the session cursor never moves. Returns the steps and
        whether the trace was fully consumed within the budget. A
        terminal error/end event is appended as the final step so an
        early exit stays a visible, comparable fact.
        """
        steps = []
        i = start + 1
        total = len(self.events)
        while i < total:
            event = self.events[i]
            if event["kind"] == "line":
                if budget is not None and len(steps) >= budget:
                    return steps, False
                steps.append(event)
            i += 1
        last = self.events[-1]
        if last["kind"] in ("error", "end") and (not steps or steps[-1] is not last):
            steps.append(last)
        return steps, True

    @staticmethod
    def _watched_values(event: dict, variables: list[str]) -> dict:
        """Resolve watched variable names against an event's stack.

        Lookup follows runtime scoping (innermost frame first); a name
        absent from every live frame is reported as None, which is a
        real difference when the other side does have a value.
        """
        frames = event.get("stack", [])
        values = {}
        for name in variables:
            found = None
            for frame in reversed(frames):
                for var in frame["locals"]:
                    if var["name"] == name:
                        found = var["value"]["display"]
                        break
                if found is not None:
                    break
            values[name] = found
        return values

    @staticmethod
    def _frame_facts(event: dict) -> list:
        return [
            (
                frame["name"],
                tuple(
                    (var["name"], var["value"]["display"])
                    for var in frame["locals"]
                ),
            )
            for frame in event.get("stack", [])
        ]

    def locate_divergence(
        self,
        other: "DebugSession",
        watch: dict | None = None,
        left_budget: int | None = None,
        right_budget: int | None = None,
        context: int = 3,
    ) -> dict:
        """Locate the earliest corresponding position where two branches differ.

        Both sides are explored forward from their current pause cursor,
        each within its own step budget. Steps are aligned semantically
        by (call path, normalized statement text, occurrence ordinal),
        never by raw step count, so a changed recursion base case, an
        extra loop iteration or an early exception cannot be silently
        aligned onto unrelated positions. Only the watched facts (the
        specified output and/or variables) decide divergence. Positions
        that exist on only one side are reported as non-correspondable,
        never claimed to be equal. When a budget runs out before the
        trace is consumed, the explored range is preserved and the
        result is budget_exhausted rather than no-divergence.
        """
        watch = watch or {}
        watch_output = bool(watch.get("output", True))
        variables = watch.get("variables", None)  # None = compare all locals
        if variables is not None:
            variables = [str(v) for v in variables]

        left_steps, left_full = self._explored_steps(self.cursor, left_budget)
        right_steps, right_full = other._explored_steps(other.cursor, right_budget)
        left_lines = self.source.splitlines()
        right_lines = other.source.splitlines()

        # Pair the k-th occurrence of each semantic key on the left with
        # the k-th occurrence of the same key on the right.
        right_map = {}
        occurrence: dict = {}
        for step in right_steps:
            key = other._step_key(step, right_lines)
            ordinal = occurrence.get(key, 0)
            occurrence[key] = ordinal + 1
            right_map[(key, ordinal)] = step
        occurrence = {}
        matched: list[tuple[dict, dict]] = []
        left_only: list[dict] = []
        for step in left_steps:
            key = self._step_key(step, left_lines)
            ordinal = occurrence.get(key, 0)
            occurrence[key] = ordinal + 1
            counterpart = right_map.get((key, ordinal))
            if counterpart is None:
                left_only.append(step)
            else:
                matched.append((step, counterpart))
        matched_right = {id(step) for _, step in matched}
        right_only = [s for s in right_steps if id(s) not in matched_right]

        def brief(session, event):
            return {
                "index": event["index"],
                "kind": event["kind"],
                "line": event.get("line"),
                "sourceHash": session.source_hash,
                "output": event.get("output", ""),
                "stack": event.get("stack", []),
                "message": event.get("message"),
            }

        def surroundings(session, steps, pos):
            lo = max(0, pos - context)
            hi = min(len(steps), pos + context + 1)
            return {
                "before": [brief(session, s) for s in steps[lo:pos]],
                "after": [brief(session, s) for s in steps[pos + 1:hi]],
            }

        result = {
            "status": "equal",
            "diverged": False,
            "watch": {"output": watch_output, "variables": variables},
            "matchedPairs": 0,
            "left": {
                "id": self.id,
                "name": self.name,
                "sourceHash": self.source_hash,
                "startCursor": self.cursor,
                "budget": left_budget,
                "exploredSteps": len(left_steps),
                "fullyExplored": left_full,
            },
            "right": {
                "id": other.id,
                "name": other.name,
                "sourceHash": other.source_hash,
                "startCursor": other.cursor,
                "budget": right_budget,
                "exploredSteps": len(right_steps),
                "fullyExplored": right_full,
            },
            "divergence": None,
            "unmatched": {
                "leftCount": len(left_only),
                "rightCount": len(right_only),
                "leftSample": [brief(self, s) for s in left_only[:5]],
                "rightSample": [brief(other, s) for s in right_only[:5]],
            },
        }

        for pair_index, (left_step, right_step) in enumerate(matched):
            reasons = []
            variable_diff = {}
            if watch_output and left_step.get("output", "") != right_step.get(
                "output", ""
            ):
                reasons.append("output")
            if variables is None:
                if self._frame_facts(left_step) != self._frame_facts(right_step):
                    reasons.append("variables")
            elif variables:
                left_values = self._watched_values(left_step, variables)
                right_values = self._watched_values(right_step, variables)
                variable_diff = {
                    name: {"left": left_values[name], "right": right_values[name]}
                    for name in variables
                    if left_values[name] != right_values[name]
                }
                if variable_diff:
                    reasons.append("variables")
            result["matchedPairs"] = pair_index + 1
            if not reasons:
                continue
            left_pos = left_steps.index(left_step)
            right_pos = right_steps.index(right_step)
            result.update({
                "status": "diverged",
                "diverged": True,
                "divergence": {
                    "pairIndex": pair_index,
                    "reasons": reasons,
                    "variables": variable_diff,
                    "left": brief(self, left_step),
                    "right": brief(other, right_step),
                    "leftContext": surroundings(self, left_steps, left_pos),
                    "rightContext": surroundings(other, right_steps, right_pos),
                },
            })
            return result

        # Every corresponding pair agreed on the watched facts. What
        # remains decides the honest status: unexplored budget tail,
        # non-correspondable positions, or true equality.
        if not left_full or not right_full:
            result["status"] = "budget_exhausted"
        elif left_only or right_only:
            result["status"] = "unmatched"
        return result

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


class ComparisonStore:
    """Persists saved branch comparisons as reopenable work results.

    A saved comparison pins the full compare result computed at save
    time: both sides' program revision hashes, chosen pause boundaries,
    cursors at save time, and the reported events with their call stacks
    and variable values. Later stepping, continuing, restarting or even
    deleting one of the branches never rewrites a saved record; reopening
    returns the pinned snapshot, with live branch freshness reported
    separately by the server. Multiple comparisons between the same pair
    of branches coexist as independent records.
    """

    def __init__(self, directory: Path = COMPARISONS_DIR):
        self.directory = directory

    def save(self, result: dict) -> dict:
        record = {
            "id": uuid.uuid4().hex[:12],
            "created_at": time.time(),
            "left_id": result["left"]["id"],
            "right_id": result["right"]["id"],
            "result": result,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{record['id']}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        return record

    def _read(self, comparison_id: str) -> dict:
        path = self.directory / f"{comparison_id}.json"
        return json.loads(path.read_text())

    def get(self, comparison_id: str) -> dict:
        return self._read(comparison_id)

    def delete(self, comparison_id: str) -> None:
        path = self.directory / f"{comparison_id}.json"
        if path.exists():
            path.unlink()

    @staticmethod
    def _summary(record: dict) -> dict:
        result = record["result"]
        return {
            "id": record["id"],
            "created_at": record.get("created_at"),
            "leftId": record["left_id"],
            "rightId": record["right_id"],
            "leftName": result["left"]["name"],
            "rightName": result["right"]["name"],
            "status": result["status"],
            "reason": result.get("reason"),
            "index": result.get("index"),
            "leftBoundary": result["left"]["boundary"],
            "rightBoundary": result["right"]["boundary"],
            "leftHash": result["left"]["sourceHash"],
            "rightHash": result["right"]["sourceHash"],
        }

    def list(self) -> list[dict]:
        if not self.directory.exists():
            return []
        summaries = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                record = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError, KeyError):
                continue
            summaries.append(self._summary(record))
        return summaries
