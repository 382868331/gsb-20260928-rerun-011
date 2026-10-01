from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .debugger import DebugSession, SessionStore, SourceError

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
    breakpoints: list[int] = []
    migrations: list[dict] | None = None


class ForkBody(BaseModel):
    name: str = ""


class CompareBody(BaseModel):
    otherId: str


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


@app.post("/api/sessions/{session_id}/branches")
def fork_branch(session_id: str, body: ForkBody):
    session = _get(session_id)
    child = store.fork(session, body.name)
    return {"branch": child.state(), "siblings": store.workspace_branches(child.workspace_id)}


@app.get("/api/sessions/{session_id}/branches")
def list_branches(session_id: str):
    session = _get(session_id)
    return {
        "workspaceId": session.workspace_id,
        "branches": store.workspace_branches(session.workspace_id),
    }


@app.delete("/api/sessions/{session_id}/branches/{branch_id}")
def abandon_branch(session_id: str, branch_id: str):
    parent = _get(session_id)
    if not (store.directory / f"{branch_id}.json").exists():
        raise HTTPException(status_code=404, detail="分支不存在")
    store.abandon(branch_id)
    return {
        "workspaceId": parent.workspace_id,
        "branches": store.workspace_branches(parent.workspace_id),
    }


@app.post("/api/sessions/{session_id}/compare")
def compare_branches(session_id: str, body: CompareBody):
    session = _get(session_id)
    try:
        other = store.get(body.otherId)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="对比分支不存在")
    if other.workspace_id != session.workspace_id:
        raise HTTPException(status_code=400, detail="只能比较同一暂停现场派生的分支")
    return DebugSession.compare_facts(session, other)


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
        result = session.preview_source_change(body.source)
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # A pure read of the candidate source: the persisted session is untouched.
    state = session.state()
    state["sourceChanged"] = result["changed"]
    state["migration"] = result
    return state


@app.post("/api/sessions/{session_id}/source/commit")
def commit_source(session_id: str, body: SourceCommitBody):
    session = _get(session_id)
    try:
        result = session.commit_source_change(
            body.source, body.breakpoints, body.migrations
        )
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    store.touch(session)
    state = session.state()
    state["sourceChanged"] = result["changed"]
    return state


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="前端尚未构建")
