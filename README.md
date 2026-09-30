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
- 更新源程序先通过 `POST /api/sessions/{id}/source/preview` 做无副作用校验。
  词法或语法错误不会改动旧断点、调用栈、游标和当前执行位置。
- 预览按改写后的行对齐结果列出 `retained_breakpoints` 与
  `reconfirm_breakpoints`；用户取消则编辑器和调试会话都回到原状态。
- 只有 `POST /api/sessions/{id}/source/commit` 接受新程序后，才重置执行轨迹、
  切换源码并应用已确认的断点；旧栈帧不会被展示为新程序的执行结果。
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
