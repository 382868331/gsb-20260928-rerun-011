from __future__ import annotations

import json
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..debugger import SessionError, SessionStore, StepKind
from ..debugger.session import compile_source

def _error(exc: SessionError) -> JSONResponse:
    status = 404 if exc.code == 'not_found' else 400
    if exc.code in ('closed', 'finished'):
        status = 409
    return JSONResponse(
        {'error': exc.message, 'code': exc.code, 'line': exc.line},
        status_code=status,
    )


async def _json(request: Request) -> dict:
    try:
        return await request.json()
    except json.JSONDecodeError:
        raise SessionError('请求体不是合法的 JSON', code='bad_request')


def create_routes(store: SessionStore):
    async def list_sessions(request: Request):
        return JSONResponse({'sessions': store.list_summaries()})

    async def create_session(request: Request):
        payload = await _json(request)
        source = payload.get('source', '')
        if not isinstance(source, str) or not source.strip():
            raise SessionError('源程序不能为空', code='empty_source')
        name = str(payload.get('name') or '未命名会话')
        breakpoints = payload.get('breakpoints') or []
        session = store.create(source, name=name, breakpoints=breakpoints)
        return JSONResponse(session.state(), status_code=201)

    async def check_source(request: Request):
        payload = await _json(request)
        source = payload.get('source', '')
        result = dict(store.source_status(source))
        try:
            compile_source(source)
            result['compiles'] = True
        except SessionError as exc:
            result['compiles'] = False
            result['compile_error'] = {
                'message': exc.message,
                'line': exc.line,
                'code': exc.code,
            }
        return JSONResponse(result)

    async def session_state(request: Request):
        session = store.get(request.path_params['session_id'])
        return JSONResponse(session.state())

    async def update_breakpoints(request: Request):
        payload = await _json(request)
        lines = payload.get('lines') or []
        if not isinstance(lines, list):
            raise SessionError('lines 必须是数组', code='bad_request')
        session = store.update(
            request.path_params['session_id'],
            lambda s: s.set_breakpoints(lines),
        )
        return JSONResponse(session.state())

    async def step(request: Request):
        kind = request.path_params['kind']
        session = store.update(
            request.path_params['session_id'],
            lambda s: s.step(StepKind(kind)),
        )
        return JSONResponse(session.state())

    async def resume(request: Request):
        session = store.update(
            request.path_params['session_id'], lambda s: s.resume()
        )
        return JSONResponse(session.state())

    async def save(request: Request):
        # State is persisted on every mutation; this endpoint makes the save
        # action explicit and returns the summary used by the UI.
        session = store.get(request.path_params['session_id'])
        return JSONResponse({'saved': True, 'summary': {
            'id': session.id,
            'name': session.name,
            'status': session.status,
            'cursor': session.cursor,
            'event_count': len(session.events),
        }})

    async def delete_session(request: Request):
        store.delete(request.path_params['session_id'])
        return JSONResponse({'deleted': True})

    return [
        Route('/api/sessions', list_sessions, methods=['GET']),
        Route('/api/sessions', create_session, methods=['POST']),
        Route('/api/source-check', check_source, methods=['POST']),
        Route('/api/sessions/{session_id}', session_state, methods=['GET']),
        Route('/api/sessions/{session_id}', delete_session, methods=['DELETE']),
        Route('/api/sessions/{session_id}/breakpoints',
              update_breakpoints, methods=['PUT']),
        Route('/api/sessions/{session_id}/step/{kind}', step, methods=['POST']),
        Route('/api/sessions/{session_id}/resume', resume, methods=['POST']),
        Route('/api/sessions/{session_id}/save', save, methods=['POST']),
    ]


def create_app(data_dir: str | None = None) -> Starlette:
    store = SessionStore(data_dir)

    async def http_error(request: Request, exc: Exception):
        if isinstance(exc, SessionError):
            return _error(exc)
        return JSONResponse(
            {'error': str(exc), 'code': 'internal'}, status_code=500
        )

    return Starlette(
        routes=create_routes(store),
        exception_handlers={SessionError: http_error, Exception: http_error},
    )
