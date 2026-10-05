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

## 分支与比较

- 暂停中可 POST /api/sessions/{id}/fork 派生实验分支：程序未变时
  分支继承当前暂停游标、断点与完整栈快照，双方后续单步/继续互不影响；
  携带修改后的程序时先整体编译（失败返回 400，父分支与兄弟分支原样
  保留），确认后的断点迁移关系（bpMigrations）随分支记录持久化。
- 放弃分支（DELETE）只删除该分支记录，可回到父分支原暂停现场继续。
- GET /api/sessions/{id}/pause-boundaries 返回该分支已经实际走到、
  可作为比较边界的暂停位置（行事件）；尚未发生的事件不会出现。
- GET /api/sessions/{id}/compare/{otherId} 只比较两侧在各自选定的
  已保存暂停边界（?leftBoundary=&rightBoundary=，默认各自当前游标）
  之前**实际发生过**的执行事实（暂停步的事件类型、累计输出、逐帧
  变量值与调用链）。两侧不必齐步：
  - `identical`：两侧都已到终态且全部执行事实一致；
  - `waiting`：公共已执行部分一致，但至少一侧尚未运行到所选位置，
    后续明确标为等待，绝不把预先知道的未来行为当成已发生的事实；
  - `ahead`：公共部分一致，一侧只是多执行了若干循环/递归步，
    另一侧该位置尚未到达（结果中该侧 event 为 null），不算是分歧；
  - `diverged`：同一暂停步序号上确实出现不同（kind/output/variables），
    返回双方当时的事件、变量与完整调用现场。
  内部 bookkeeping 事件（stmt_end/call/return）不按下标齐步对齐，
  只按用户可暂停的行事件序列比较，因此插入代码行或调用层次不同
  不会把相同行号误判为同一执行事件。
- 结果携带两侧各自的程序修订哈希、所选 boundary 与当前 cursor；
  继续其中一个分支后，旧比较仍钉在原 boundary（boundary 与 cursor
  不一致时前端提示“旧边界”），重新比较才纳入新事件。放弃分支或
  重开都不改动另一分支的暂停现场；未完成的比较只会显示 waiting/
  ahead，不会被伪装成一致。
- 一次比较可保存为可重开的工作结果：POST
  /api/sessions/{id}/compare/{otherId}/save 把当时的双方修订哈希、
  所选边界、cursor、调用现场与变量输出整体钉入
  `backend/comparisons/`；之后单步、继续、切换分支甚至放弃其中
  一侧都不改写该记录。GET /api/comparisons 列出全部记录（同一对
  分支可保存多次并在其间切换），GET /api/comparisons/{cid} 返回
  钉住的结果并附带各分支当前新鲜度（cursor / 是否已放弃），
  DELETE 删除单条记录。两侧都在零事件时保存的比较保持 waiting，
  空集合一致不会被解释成整个程序一致。
- 页面重开后自动恢复上次的调试会话与比较选择（会话 id 与比较
  记录 id 记录在 localStorage，记录本体在后端持久化）。
- 保存全部分支后重开，各分支分别恢复未结束的执行，人工确认过
  的断点迁移关系一并保留。

## 语义分歧定位

- 从共同暂停点派生两个代码修订后，POST
  /api/sessions/{id}/divergence/{otherId} 在两侧**各自的执行预算**
  （leftBudget/rightBudget，空为不限）内寻找最早可对应但值不同的
  执行位置；观察项由 variables（空=全部变量）与 output 指定。
- 对齐按语义对应关系（调用路径 + 语句文本 + 第几次出现），绝不按
  步数：循环多一次、递归更深或一侧提前结束只会产生"无法对应"
  （unmatched）的位置，如实列出而不会被说成相同；对应上的部分
  才逐对比较观察值。
- 结果状态：diverged（给出最早分歧两侧的事件、不同变量、完整
  调用链与分歧前后各 3 个暂停步的调用现场）/ identical（预算
  穷尽且全部对应一致）/ unmatched（对应部分一致但存在无法对应
  的位置）/ inconclusive（预算截断，保留已探索范围，不宣称不
  存在分歧）。
- 分歧位置可通过 POST /api/sessions/{id}/fork 携带 atCursor 直接
  派生新的调查分支，原分支的执行与记录保持不变；atCursor 超出
  已记录范围返回 400。

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
