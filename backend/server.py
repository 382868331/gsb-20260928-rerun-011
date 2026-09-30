from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .debugger import SessionStore, SourceError

app = FastAPI(title="MiniLang 可视化调试器")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = SessionStore()

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class SourceBody(BaseModel):
    source: str


class SourceCommitBody(BaseModel):
    source: str
    breakpoints: list[int] | None = None


class BreakpointsBody(BaseModel):
    lines: list[int]


class StepBody(BaseModel):
    mode: str = "into"  # "into" | "over"


def _get(session_id: str):
    try:
        return store.get(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="会话不存在")


@app.post("/api/sessions")
def create_session(body: SourceBody):
    try:
        session = store.create(body.source)
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return session.state()


@app.get("/api/sessions")
def list_sessions():
    return {"sessions": store.list_saved()}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    return _get(session_id).state()


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    _get(session_id)
    store.delete(session_id)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/step")
def step(session_id: str, body: StepBody):
    session = _get(session_id)
    if body.mode == "over":
        session.step_over()
    else:
        session.step_into()
    store.touch(session)
    return session.state()


@app.post("/api/sessions/{session_id}/continue")
def continue_(session_id: str):
    session = _get(session_id)
    session.continue_()
    store.touch(session)
    return session.state()


@app.post("/api/sessions/{session_id}/restart")
def restart(session_id: str):
    session = _get(session_id)
    session.restart()
    store.touch(session)
    return session.state()


@app.put("/api/sessions/{session_id}/breakpoints")
def set_breakpoints(session_id: str, body: BreakpointsBody):
    session = _get(session_id)
    session.set_breakpoints(body.lines)
    store.touch(session)
    return session.state()


@app.post("/api/sessions/{session_id}/source/preview")
def preview_source(session_id: str, body: SourceBody):
    session = _get(session_id)
    try:
        return session.preview_source_update(body.source)
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/sessions/{session_id}/source")
def update_source(session_id: str, body: SourceBody):
    session = _get(session_id)
    try:
        result = session.update_source(body.source)
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    store.touch(session)
    state = session.state()
    state["sourceChanged"] = result["changed"]
    state["invalidatedBreakpoints"] = result["invalidated_breakpoints"]
    return state


@app.post("/api/sessions/{session_id}/source/commit")
def commit_source(session_id: str, body: SourceCommitBody):
    session = _get(session_id)
    try:
        result = session.commit_source_update(body.source, body.breakpoints)
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    store.touch(session)
    state = session.state()
    state["sourceChanged"] = result["changed"]
    state["invalidatedBreakpoints"] = result["invalidated_breakpoints"]
    state["migratedBreakpoints"] = result.get("migrated_breakpoints", [])
    return state


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="前端尚未构建")
