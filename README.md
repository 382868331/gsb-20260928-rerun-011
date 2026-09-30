# MiniLang 可视化调试器

在既有语言执行内核（事件追踪）之上，提供可单步、断点、查看变量的
调试会话与调试页面。

## 结构

- `backend/minilang/`：词法、语法、执行内核（产生带完整栈快照的事件流）。
- `backend/debugger.py`：调试会话。基于确定性事件流做游标式回放，
  支持单步（into/over）、继续到断点、暂停中调整断点、会话保存/重开。
- `backend/server.py`：FastAPI 接口（`/api/sessions/...`），并托管前端构建产物。
- `frontend/`：React 调试页面（编辑器 + 断点行槽 + 调用栈 + 变量 + 输出 + 错误链）。

## 关键语义

- 每个暂停事件携带逐帧栈快照，递归中的同名局部变量始终跟随当前栈帧。
- 继续/单步后前端整体刷新状态，不会残留旧暂停位置的变量值。
- 源程序变化分两步：`POST /api/sessions/{id}/source/preview` 先编译候选程序
  并返回断点迁移预览（`reusable` 可直接沿用 / `confirm` 需重新确认，
  歧义位置给出全部候选行）。编译失败返回 400，旧断点、栈帧、游标和
  当前执行位置原样保留；连续提交错误内容也不会改动会话。
  只有 `POST /api/sessions/{id}/source/commit` 携带用户确认的断点后，
  才会原子切换到新程序的调试状态（从头开始）；取消即留在原会话。
  保存重开后再切换同样不会把旧栈帧展示成新程序的执行结果。
- 程序退出（正常或出错）后仍可查看最后一次错误与完整调用链。

## 运行

```bash
cd frontend && npm run build && cd ..
.venv/bin/python -m uvicorn backend.server:app --port 8000
```

开发模式：`cd frontend && npm run dev`（已配置 `/api` 代理到 8000）。

## 测试

```bash
.venv/bin/python -m pytest backend/tests -q
```
