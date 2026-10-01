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

    def __init__(
        self,
        source: str,
        session_id: str | None = None,
        *,
        workspace_id: str | None = None,
        parent_id: str | None = None,
        name: str = "main",
        forked_from: int | None = None,
    ):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.created_at = time.time()
        self.workspace_id = workspace_id or self.id
        self.parent_id = parent_id
        self.name = name
        self.forked_from = forked_from
        self.breakpoints: set[int] = set()
        self.cursor = -1
        _, self.events = self._build_trace(source)
        self.source = source
        self.source_hash = source_hash(source)
        self.confirmed_migrations: list[dict] = []

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

    def commit_source_change(
        self,
        source: str,
        breakpoints: list[int],
        migrations: list[dict] | None = None,
    ) -> dict:
        """Atomically switch to an already validated new program.

        Compilation happens before any field is touched, so even a
        last-moment failure keeps the old session intact. On success the
        old trace/cursor/stack are replaced together with the source and
        the explicitly confirmed breakpoints; the new session always
        starts before its first line, never on an old frame.
        """
        if source_hash(source) == self.source_hash:
            return {"changed": False}
        old_hash = self.source_hash
        # Build the whole new trace first; this raises before mutation.
        _, events = self._build_trace(source)
        confirmed = []
        for item in migrations or []:
            old_line = item.get("oldLine")
            new_line = item.get("newLine")
            if (
                isinstance(old_line, int)
                and isinstance(new_line, int)
                and old_line > 0
                and new_line > 0
            ):
                confirmed.append({"oldLine": old_line, "newLine": new_line})
        self.events = events
        self.source = source
        self.source_hash = source_hash(source)
        self.cursor = -1
        self.breakpoints = {
            ln for ln in breakpoints if isinstance(ln, int) and ln > 0
        }
        if confirmed:
            self.confirmed_migrations.append({
                "fromHash": old_hash,
                "toHash": self.source_hash,
                "mappings": confirmed,
            })
        return {"changed": True}

    def fork_branch(self, name: str) -> "DebugSession":
        """Derive an independent experiment from the current pause site."""
        child = DebugSession(
            self.source,
            workspace_id=self.workspace_id,
            parent_id=self.id,
            name=name.strip() or f"实验 {int(time.time()) % 100000}",
            forked_from=self.cursor,
        )
        child.created_at = time.time()
        child.breakpoints = set(self.breakpoints)
        child.cursor = self.cursor
        child.confirmed_migrations = [dict(m) for m in self.confirmed_migrations]
        return child

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
            "workspaceId": self.workspace_id,
            "branchName": self.name,
            "parentId": self.parent_id,
            "forkedFrom": self.forked_from,
            "cursor": self.cursor,
            "status": status,
            "line": event.get("line"),
            "kind": event["kind"],
            "output": event.get("output", ""),
            "stack": event.get("stack", []),
            "breakpoints": sorted(self.breakpoints),
            "source": self.source,
            "sourceHash": self.source_hash,
            "confirmedMigrations": self.confirmed_migrations,
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
            "workspace_id": self.workspace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "forked_from": self.forked_from,
            "source": self.source,
            "breakpoints": sorted(self.breakpoints),
            "cursor": self.cursor,
            "confirmed_migrations": self.confirmed_migrations,
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
        session.workspace_id = record.get("workspace_id", record["id"])
        session.parent_id = record.get("parent_id")
        session.name = record.get("name", "main")
        session.forked_from = record.get("forked_from")
        session.confirmed_migrations = list(record.get("confirmed_migrations", []))
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

    @staticmethod
    def list_workspace(
        workspace_id: str, directory: Path = SESSIONS_DIR
    ) -> list[dict]:
        if not directory.exists():
            return []
        branches = []
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if record.get("workspace_id", record.get("id")) != workspace_id:
                continue
            source_lines = record.get("source", "").splitlines()
            branches.append({
                "id": record["id"],
                "parentId": record.get("parent_id"),
                "name": record.get("name", "main"),
                "createdAt": record.get("created_at"),
                "forkedFrom": record.get("forked_from"),
                "sourceHash": source_hash(record.get("source", "")),
                "cursor": record.get("cursor", -1),
                "breakpoints": record.get("breakpoints", []),
                "revisionPreview": source_lines[0].strip() if source_lines else "",
            })
        return branches


    # ---- execution-fact comparison ----

    def executed_facts(self) -> list[dict]:
        """Line-pause facts already reached, each tagged by program revision.

        Revision tagging matters after source edits: the same numeric line
        in two revisions must not be treated as the same execution step.
        """
        lines = self.source.splitlines()
        facts = []
        last_output = ""
        post_by_index: dict[int, dict] = {}
        pending_line_indexes: list[int] = []
        for index, evt in enumerate(self.events):
            if evt["kind"] == "line" and index <= self.cursor:
                pending_line_indexes.append(index)
            elif (
                evt["kind"] == "stmt_end"
                and pending_line_indexes
                and pending_line_indexes[-1] <= self.cursor
            ):
                # stmt_end closes the most recently opened statement region.
                post_by_index[pending_line_indexes.pop()] = evt
            elif evt["kind"] in ("end", "error"):
                break
        for index, evt in enumerate(self.events):
            if index > self.cursor or evt["kind"] != "line":
                continue
            output = evt.get("output", "")
            post_event = post_by_index.get(index)
            post_output = post_event.get("output", output) if post_event else output
            facts.append({
                "seq": len(facts),
                "eventIndex": index,
                "sourceHash": self.source_hash,
                "line": evt["line"],
                "lineText": _line_text(lines, evt["line"]),
                "output": output,
                "outputDelta": (
                    output[len(last_output):]
                    if output.startswith(last_output)
                    else output
                ),
                "frames": [
                    {
                        "depth": depth,
                        "name": frame["name"],
                        "locals": {
                            var["name"]: var["value"]["display"]
                            for var in frame.get("locals", [])
                        },
                    }
                    for depth, frame in enumerate(evt.get("stack", []))
                ],
                "postOutput": post_output,
                "postOutputDelta": (
                    post_output[len(last_output):]
                    if post_output.startswith(last_output)
                    else post_output
                ),
                "postFrames": [
                    {
                        "depth": depth,
                        "name": frame["name"],
                        "locals": {
                            var["name"]: var["value"]["display"]
                            for var in frame.get("locals", [])
                        },
                    }
                    for depth, frame in enumerate(
                        (post_event or evt).get("stack", [])
                    )
                ],
            })
            last_output = post_output
        return facts

    @staticmethod
    def compare_facts(
        self_session: "DebugSession", other: "DebugSession"
    ) -> dict:
        """Align branches by execution order and locate the first divergence."""
        facts_a = self_session.executed_facts()
        facts_b = other.executed_facts()
        aligned = []
        first_difference = None
        for seq in range(max(len(facts_a), len(facts_b))):
            fact_a = facts_a[seq] if seq < len(facts_a) else None
            fact_b = facts_b[seq] if seq < len(facts_b) else None
            difference = DebugSession._fact_difference(fact_a, fact_b)
            entry = {
                "seq": seq,
                "a": fact_a,
                "b": fact_b,
                "difference": difference,
            }
            aligned.append(entry)
            if difference is not None and first_difference is None:
                first_difference = entry
                break
        return {
            "workspaceId": self_session.workspace_id,
            "branches": {
                "a": DebugSession._branch_fact_summary(
                    self_session, len(facts_a)
                ),
                "b": DebugSession._branch_fact_summary(other, len(facts_b)),
            },
            "firstDifference": first_difference,
            "aligned": aligned,
            "executedCounts": {"a": len(facts_a), "b": len(facts_b)},
        }

    @staticmethod
    def _branch_fact_summary(session: "DebugSession", fact_count: int) -> dict:
        preview_lines = session.source.splitlines()
        return {
            "id": session.id,
            "name": session.name,
            "sourceHash": session.source_hash,
            "revisionPreview": preview_lines[0].strip() if preview_lines else "",
            "executedFacts": fact_count,
            "status": session.status(),
        }

    @staticmethod
    def _fact_difference(a: dict | None, b: dict | None) -> dict | None:
        if a is None or b is None:
            return {
                "kind": "length",
                "message": "一个分支还有未对齐的后续执行事实，另一个分支已没有记录",
            }
        revision_changed = a["sourceHash"] != b["sourceHash"]
        variable_diffs = []
        for depth in range(max(len(a["postFrames"]), len(b["postFrames"]))):
            frame_a = (
                a["postFrames"][depth]
                if depth < len(a["postFrames"])
                else None
            )
            frame_b = (
                b["postFrames"][depth]
                if depth < len(b["postFrames"])
                else None
            )
            if frame_a is None or frame_b is None:
                present = frame_b or frame_a
                variable_diffs.append({
                    "frameDepth": depth,
                    "frameName": present["name"],
                    "kind": "frame",
                    "a": None if frame_a is None else frame_a["name"],
                    "b": None if frame_b is None else frame_b["name"],
                })
                continue
            for var_name in sorted(set(frame_a["locals"]) | set(frame_b["locals"])):
                value_a = frame_a["locals"].get(var_name)
                value_b = frame_b["locals"].get(var_name)
                if value_a != value_b:
                    variable_diffs.append({
                        "frameDepth": depth,
                        "frameName": frame_a["name"],
                        "kind": "variable",
                        "name": var_name,
                        "a": value_a,
                        "b": value_b,
                    })
        output_changed = a["postOutputDelta"] != b["postOutputDelta"]
        if variable_diffs:
            return {
                "kind": "variable",
                "revisionChanged": revision_changed,
                "lineNumberSame": a["line"] == b["line"],
                "variables": variable_diffs,
                "message": "变量值第一次出现不同",
            }
        if output_changed:
            message = "输出第一次出现不同"
            if revision_changed and a["line"] == b["line"]:
                message += "；记录来自不同程序修订，行号相同不代表同一步"
            return {
                "kind": "output",
                "revisionChanged": revision_changed,
                "lineNumberSame": a["line"] == b["line"],
                "a": a["outputDelta"],
                "b": b["outputDelta"],
                "message": message,
            }
        if revision_changed:
            return {
                "kind": "revision",
                "lineNumberSame": a["line"] == b["line"],
                "aRevision": a["sourceHash"],
                "bRevision": b["sourceHash"],
                "message": (
                    "两条记录来自不同程序修订；即使行号相同，也不是同一步"
                    if a["line"] == b["line"]
                    else "两条记录来自不同程序修订"
                ),
            }
        return None


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

    def fork(self, session: DebugSession, name: str) -> DebugSession:
        child = session.fork_branch(name)
        self.sessions[child.id] = child
        child.save(self.directory)
        return child

    def workspace_branches(self, workspace_id: str) -> list[dict]:
        return DebugSession.list_workspace(workspace_id, self.directory)

    def abandon(self, session_id: str) -> None:
        """Permanently discard one experiment without touching siblings.

        A parent is preserved independently: abandoning a failed branch
        simply returns the user to any still-saved pause site.
        """
        self.delete(session_id)

    def delete(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        path = self.directory / f"{session_id}.json"
        if path.exists():
            path.unlink()

    def list_saved(self) -> list[dict]:
        return DebugSession.list_saved(self.directory)
