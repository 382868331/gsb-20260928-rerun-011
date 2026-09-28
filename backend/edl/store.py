"""Persistent session storage.

Every session keeps its immutable source plus an append-only action log.  A
session is restored by parsing the stored source and replaying the actions in
order, which exactly reconstructs the execution state and event history.
"""

from __future__ import annotations

import json
import os
import threading
import uuid

from .debugger import Session, create_session, source_hash
from .parser import ParseEduError


class StoreError(Exception):
    pass


class SessionStore:
    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        for session_id in os.listdir(directory):
            path = os.path.join(directory, session_id)
            if os.path.isdir(path):
                try:
                    self._sessions[session_id] = self._load(session_id)
                except (OSError, ValueError, ParseEduError):
                    # Corrupt/old sessions must not prevent the server starting.
                    continue

    def create(self, source: str) -> Session:
        session_id = uuid.uuid4().hex[:12]
        session = create_session(session_id, source)
        with self._lock:
            os.makedirs(self._dir(session_id), exist_ok=True)
            self._write_source(session)
            session.actions = []  # type: ignore[attr-defined]
            self._sessions[session_id] = session
            session.start()
            self._append_action(session, {"action": "start"})
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def require(self, session_id: str) -> Session:
        session = self.get(session_id)
        if session is None:
            raise StoreError(f"session {session_id!r} does not exist")
        return session

    def assert_source_matches(self, session: Session, source: str) -> None:
        if source_hash(source) != session.src_hash:
            raise StoreError(
                "source changed: the old session and its breakpoints no longer apply; start a new session"
            )

    def apply_action(self, session: Session, action: str, payload: dict | None = None) -> None:
        with self._lock:
            if action == "start":
                session.start()
            elif action == "stepInto":
                session.step_into()
            elif action == "stepOver":
                session.step_over()
            elif action == "continue":
                session.continue_run()
            elif action == "setBreakpoint":
                result = session.set_breakpoint(int(payload["line"]))
                if not result["ok"]:
                    raise StoreError(result["error"])
            elif action == "clearBreakpoint":
                session.clear_breakpoint(int(payload["line"]))
            else:
                raise StoreError(f"unknown action {action!r}")
            self._append_action(session, {"action": action, **(payload or {})})

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "sessionId": session.session_id,
                    "status": session.status,
                    "srcHash": session.src_hash,
                    "eventCount": len(session.events),
                }
                for session in self._sessions.values()
            ]

    def _dir(self, session_id: str) -> str:
        return os.path.join(self.directory, session_id)

    def _source_path(self, session_id: str) -> str:
        return os.path.join(self._dir(session_id), "source.edl")

    def _log_path(self, session_id: str) -> str:
        return os.path.join(self._dir(session_id), "actions.jsonl")

    def _write_source(self, session: Session) -> None:
        tmp = self._source_path(session.session_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(session.source)
        os.replace(tmp, self._source_path(session.session_id))

    def _append_action(self, session: Session, record: dict) -> None:
        actions = getattr(session, "actions", None)
        if actions is None:
            actions = []
            session.actions = actions  # type: ignore[attr-defined]
        actions.append(record)
        with open(self._log_path(session.session_id), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

    def _load(self, session_id: str) -> Session:
        with open(self._source_path(session_id), encoding="utf-8") as handle:
            source = handle.read()
        session = create_session(session_id, source)
        session.actions = []  # type: ignore[attr-defined]
        log_path = self._log_path(session_id)
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    record = json.loads(raw)
                    action = record.pop("action")
                    if action == "start":
                        session.start()
                    elif action == "stepInto":
                        session.step_into()
                    elif action == "stepOver":
                        session.step_over()
                    elif action == "continue":
                        session.continue_run()
                    elif action in ("setBreakpoint", "clearBreakpoint"):
                        method = session.set_breakpoint if action == "setBreakpoint" else session.clear_breakpoint
                        result = method(int(record["line"]))
                        if not result["ok"]:
                            raise StoreError(result["error"])
                    session.actions.append({"action": action, **record})  # type: ignore[attr-defined]
        return session
