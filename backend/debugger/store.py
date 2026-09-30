from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .session import DebugSession, SessionError, source_hash


class SessionStore:
    """Thread-safe sessions persisted as JSON files.

    Persistence makes pause -> save -> reopen -> continue survive restarts.
    """

    def __init__(self, directory: str | os.PathLike | None = None):
        base = Path(directory or os.environ.get(
            'MINILANG_DEBUG_DATA', str(Path.cwd() / 'debugger_data')
        ))
        self.dir = base / 'sessions'
        self.dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, DebugSession] = {}
        self._lock = threading.Lock()
        self._load_all()

    def create(self, source: str, name: str = '未命名会话',
               breakpoints: list[int] | set[int] | None = None,
               session_id: str | None = None) -> DebugSession:
        with self._lock:
            session = DebugSession.create(
                source=source,
                name=name,
                breakpoints=set(breakpoints or []),
                session_id=session_id,
            )
            self._sessions[session.id] = session
            self._persist(session)
            return session

    def get(self, session_id: str) -> DebugSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionError(f'会话 {session_id} 不存在', code='not_found')
            return session

    def list_summaries(self) -> list[dict]:
        with self._lock:
            return [
                {
                    'id': s.id,
                    'name': s.name,
                    'status': s.status,
                    'source_hash': s.source_hash,
                    'cursor': s.cursor,
                    'event_count': len(s.events),
                    'breakpoints': sorted(s.breakpoints),
                    'current_line': (s.current_event or {}).get('line'),
                }
                for s in self._sessions.values()
            ]

    def update(self, session_id: str, mutate) -> DebugSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionError(f'会话 {session_id} 不存在', code='not_found')
            mutate(session)
            self._persist(session)
            return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                raise SessionError(f'会话 {session_id} 不存在', code='not_found')
            session.close()
            path = self._path(session_id)
            if path.exists():
                path.unlink()

    def source_status(self, source: str) -> dict:
        digest = source_hash(source)
        matching = [
            s.id for s in self._sessions.values() if s.source_hash == digest
        ]
        stale = [
            {'id': s.id, 'name': s.name}
            for s in self._sessions.values() if s.source_hash != digest
        ]
        return {
            'source_hash': digest,
            'matching_session_ids': matching,
            'stale': stale,
        }

    def _path(self, session_id: str) -> Path:
        return self.dir / f'{session_id}.json'

    def _persist(self, session: DebugSession) -> None:
        payload = {
            'id': session.id,
            'name': session.name,
            'source': session.source,
            'events': session.events,
            'breakpoints': sorted(session.breakpoints),
            'rejected_breakpoints': {
                str(line): reason
                for line, reason in session.rejected_breakpoints.items()
            },
            'cursor': session.cursor,
            'status': session.status,
            'source_hash': session.source_hash,
        }
        tmp = self._path(session.id).with_suffix('.json.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, self._path(session.id))

    def _load_all(self) -> None:
        for path in sorted(self.dir.glob('*.json')):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            session = DebugSession(
                id=payload['id'],
                name=payload.get('name', '未命名会话'),
                source=payload['source'],
                events=payload['events'],
                breakpoints=set(payload.get('breakpoints', [])),
                rejected_breakpoints={
                    int(k): v
                    for k, v in payload.get('rejected_breakpoints', {}).items()
                },
                cursor=payload.get('cursor', -1),
                status=payload.get('status', 'paused'),
                source_hash=payload.get('source_hash', ''),
            )
            self._sessions[session.id] = session
