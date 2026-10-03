import { useEffect, useMemo, useState } from "react";
import {
  api,
  BranchInfo,
  BpMigration,
  PauseBoundary,
  CompareResult,
  ComparisonSummary,
  LiveSide,
  MigrationPreview,
  SessionState,
} from "./api";

const SAMPLE = `fun fact(n)
    let local = n * 10
    if n <= 1
        return 1
    return n * fact(n - 1)
let r = fact(4)
print(r)
`;

const STATUS_LABEL: Record<string, string> = {
  paused: "已暂停",
  finished: "已结束",
  error: "运行错误",
};

const REASON_LABEL: Record<string, string> = {
  variables: "变量值不同",
  output: "输出不同",
  kind: "事件类型不同",
  ahead: "一侧走得更远",
};

const COMPARE_STATUS_LABEL: Record<string, string> = {
  identical: "完全一致",
  waiting: "等待",
  ahead: "一侧先行",
  diverged: "首次不同",
};

// 页面重开后恢复上次会话与比较选择的本地键。
const LS_SESSION = "minilang.sessionId";
const LS_TARGET = "minilang.compareTarget";
const LS_COMPARISON = "minilang.comparisonId";

export default function App() {
  const [source, setSource] = useState(SAMPLE);
  const [session, setSession] = useState<SessionState | null>(null);
  const [selectedFrame, setSelectedFrame] = useState<number>(-1);
  const [notice, setNotice] = useState<string>("");
  const [error, setError] = useState<string>("");
  // Pending source switch: the candidate text plus per-breakpoint choices.
  // While this is set the live session still belongs to the old program.
  const [preview, setPreview] = useState<MigrationPreview | null>(null);
  const [pendingSource, setPendingSource] = useState<string>("");
  // Map of old breakpoint line -> chosen new line (or null to drop it).
  const [bpChoices, setBpChoices] = useState<Record<number, number | null>>({});
  // When true, confirming the migration banner forks a new branch
  // instead of replacing the current session.
  const [forkMode, setForkMode] = useState(false);
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [compareTarget, setCompareTarget] = useState<string>("");
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null);
  // Independently chosen saved pause boundaries for the two sides.
  // undefined = use each side's current cursor.
  const [leftBoundary, setLeftBoundary] = useState<number | undefined>(undefined);
  const [rightBoundary, setRightBoundary] = useState<number | undefined>(undefined);
  const [leftBoundaries, setLeftBoundaries] = useState<PauseBoundary[]>([]);
  const [rightBoundaries, setRightBoundaries] = useState<PauseBoundary[]>([]);
  // Saved comparison records: reopenable work results pinned at save time.
  const [savedComparisons, setSavedComparisons] = useState<ComparisonSummary[]>([]);
  // Non-empty while a saved record (not a live comparison) is displayed.
  const [activeComparisonId, setActiveComparisonId] = useState<string>("");
  const [compareLive, setCompareLive] = useState<{
    left: LiveSide;
    right: LiveSide;
  } | null>(null);

  const dirty = useMemo(
    () => session !== null && source !== session.source,
    [session, source]
  );

  useEffect(() => {
    // 每次暂停位置变化后，默认选中栈顶帧，保证变量跟随当前帧。
    setSelectedFrame(-1);
  }, [session?.line, session?.kind, session?.status]);

  async function refreshBranches() {
    try {
      const res = await api.list();
      setBranches(res.sessions);
    } catch {
      // 列表刷新失败不影响调试主流程。
    }
  }

  async function refreshComparisons() {
    try {
      const res = await api.listComparisons();
      setSavedComparisons(res.comparisons);
    } catch {
      // 比较记录列表刷新失败不影响调试主流程。
    }
  }

  useEffect(() => {
    // 页面重开：恢复上次的调试会话与比较选择。
    refreshBranches();
    refreshComparisons();
    (async () => {
      let restoredId: string | null = null;
      const sid = localStorage.getItem(LS_SESSION);
      if (sid) {
        try {
          const state = await api.get(sid);
          setSession(state);
          setSource(state.source);
          restoredId = state.id;
        } catch {
          localStorage.removeItem(LS_SESSION);
        }
      }
      const cid = localStorage.getItem(LS_COMPARISON);
      if (cid) {
        try {
          await openComparison(cid, restoredId ?? undefined);
          return;
        } catch {
          localStorage.removeItem(LS_COMPARISON);
        }
      }
      const target = localStorage.getItem(LS_TARGET);
      if (target) setCompareTarget(target);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (session) localStorage.setItem(LS_SESSION, session.id);
  }, [session?.id]);

  useEffect(() => {
    localStorage.setItem(LS_TARGET, compareTarget);
  }, [compareTarget]);

  useEffect(() => {
    localStorage.setItem(LS_COMPARISON, activeComparisonId);
  }, [activeComparisonId]);

  async function run(action: () => Promise<SessionState>) {
    setError("");
    setNotice("");
    try {
      const state = await action();
      setSession(state);
      // A displayed comparison stays pinned at its chosen boundaries:
      // continuing one branch must not rewrite the old result; the
      // comparison panel shows separately when either side moved on.
      refreshBranches();
      if (activeComparisonId) {
        refreshLive(activeComparisonId);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function refreshLive(cid: string) {
    try {
      const saved = await api.getComparison(cid);
      setCompareLive(saved.live);
    } catch {
      // 记录被删除等情况由列表刷新处理。
    }
  }

  const start = () => run(() => api.create(source));
  const stepInto = () => session && run(() => api.step(session.id, "into"));
  const stepOver = () => session && run(() => api.step(session.id, "over"));
  const cont = () => session && run(() => api.continue(session.id));
  const restart = () => session && run(() => api.restart(session.id));

  function toggleBreakpoint(line: number) {
    if (!session) return;
    const has = session.breakpoints.includes(line);
    const lines = has
      ? session.breakpoints.filter((l) => l !== line)
      : [...session.breakpoints, line];
    run(() => api.setBreakpoints(session.id, lines));
  }

  async function applySource(asBranch = false) {
    if (!session) return;
    setError("");
    setNotice("");
    try {
      const state = await api.previewSource(session.id, source);
      const migration = state.migration ?? null;
      if (!migration || !migration.changed) {
        setPreview(null);
        setNotice("新程序与当前会话一致，无需切换。");
        return;
      }
      setPendingSource(source);
      setPreview(migration);
      setForkMode(asBranch);
      const choices: Record<number, number | null> = {};
      for (const item of migration.migrations) {
        choices[item.oldLine] = item.newLine;
      }
      setBpChoices(choices);
    } catch (e) {
      // Parse failure: leave editor text and the whole old session intact.
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function cancelSwitch() {
    setPreview(null);
    setPendingSource("");
    setBpChoices({});
    setForkMode(false);
    if (session) {
      // Restore the editor to the program the live session actually runs.
      setSource(session.source);
    }
    setNotice("已取消切换，继续使用原调试会话。");
    setError("");
  }

  async function confirmSwitch() {
    if (!session || !preview) return;
    const lines = Object.values(bpChoices).filter(
      (ln): ln is number => ln !== null && ln !== undefined
    );
    const migrations: BpMigration[] = preview.migrations
      .filter((item) => (bpChoices[item.oldLine] ?? null) !== null)
      .map((item) => ({
        oldLine: item.oldLine,
        newLine: bpChoices[item.oldLine] as number,
        fromHash: session.sourceHash,
        toHash: preview.newSourceHash,
      }));
    try {
      const state = forkMode
        ? await api.fork(session.id, {
            source: pendingSource,
            breakpoints: lines,
            bpMigrations: migrations,
          })
        : await api.commitSource(session.id, pendingSource, lines);
      setSession(state);
      setSource(state.source);
      setPreview(null);
      setPendingSource("");
      setBpChoices({});
      setForkMode(false);
      setNotice(
        forkMode
          ? "已创建实验分支，断点迁移已应用；原暂停现场保持不变。"
          : "已切换到新程序，断点迁移已应用，会话从头开始。"
      );
      setError("");
      refreshBranches();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function forkBranch() {
    if (!session) return;
    if (dirty) {
      // 编辑器里有未应用的修改：走断点迁移预览，确认后成为新分支。
      await applySource(true);
      return;
    }
    await run(() => api.fork(session.id, {}));
    setNotice("已从当前暂停现场派生分支，双方互不影响。");
  }

  async function abandonBranch() {
    if (!session || !session.parentId) return;
    const parentId = session.parentId;
    setError("");
    setNotice("");
    try {
      await api.remove(session.id);
      const parent = await api.get(parentId);
      setSession(parent);
      setSource(parent.source);
      // 已保存的比较记录不随分支放弃而消失；只刷新其新鲜度提示。
      if (activeComparisonId) {
        refreshLive(activeComparisonId);
      } else {
        setCompareResult(null);
      }
      setNotice("已放弃实验分支，回到原暂停现场。");
      refreshBranches();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function switchBranch(id: string) {
    if (id === session?.id) return;
    setPreview(null);
    setPendingSource("");
    setBpChoices({});
    setForkMode(false);
    await run(() =>
      api.get(id).then((state) => {
        setSource(state.source);
        return state;
      })
    );
  }

  async function runCompare() {
    if (!session || !compareTarget) return;
    setError("");
    try {
      setActiveComparisonId("");
      setCompareLive(null);
      const [leftB, rightB] = await Promise.all([
        api.pauseBoundaries(session.id),
        api.pauseBoundaries(compareTarget),
      ]);
      setLeftBoundaries(leftB.boundaries);
      setRightBoundaries(rightB.boundaries);
      setCompareResult(
        await api.compare(session.id, compareTarget, {
          leftBoundary,
          rightBoundary,
        })
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveCompare() {
    if (!session || !compareTarget) return;
    setError("");
    try {
      const record = await api.saveCompare(session.id, compareTarget, {
        leftBoundary,
        rightBoundary,
      });
      // 保存后立即按记录展示：结果钉在此刻，后续执行不改写它。
      const saved = await api.getComparison(record.id);
      setCompareResult(saved.record.result);
      setCompareLive(saved.live);
      setActiveComparisonId(record.id);
      setNotice("比较已保存为可重开的工作结果，后续单步/继续/切换分支都不会改写它。");
      refreshComparisons();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function openComparison(cid: string, currentSessionId?: string) {
    setError("");
    try {
      const saved = await api.getComparison(cid);
      const result = saved.record.result;
      setCompareResult(result);
      setCompareLive(saved.live);
      setActiveComparisonId(cid);
      const sid = currentSessionId ?? session?.id;
      const other =
        result.left.id === sid ? result.right.id : result.left.id;
      setCompareTarget(other);
      setLeftBoundary(result.left.boundary);
      setRightBoundary(result.right.boundary);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeComparison(cid: string) {
    setError("");
    try {
      await api.deleteComparison(cid);
      if (activeComparisonId === cid) {
        setActiveComparisonId("");
        setCompareResult(null);
        setCompareLive(null);
      }
      refreshComparisons();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function backToLiveCompare() {
    setActiveComparisonId("");
    setCompareLive(null);
    runCompare();
  }

  async function chooseCompareTarget(id: string) {
    setCompareTarget(id);
    setCompareResult(null);
    setLeftBoundary(undefined);
    setRightBoundary(undefined);
    if (!session || !id) return;
    try {
      const [leftB, rightB] = await Promise.all([
        api.pauseBoundaries(session.id),
        api.pauseBoundaries(id),
      ]);
      setLeftBoundaries(leftB.boundaries);
      setRightBoundaries(rightB.boundaries);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const lines = source.split("\n");
  const stack = session?.stack ?? [];
  const frameIndex =
    selectedFrame >= 0 && selectedFrame < stack.length
      ? selectedFrame
      : stack.length - 1;
  const frame = frameIndex >= 0 ? stack[frameIndex] : null;
  const paused = session?.status === "paused";
  const pendingLines = preview
    ? new Set(
        Object.values(bpChoices).filter(
          (ln): ln is number => ln !== null
        )
      )
    : null;

  return (
    <div className="app">
      <header>
        <h1>MiniLang 可视化调试器</h1>
        <div className="controls">
          <button onClick={start}>▶ 启动新会话</button>
          <button onClick={stepInto} disabled={!paused}>单步</button>
          <button onClick={stepOver} disabled={!paused}>跨过</button>
          <button onClick={cont} disabled={!paused}>继续</button>
          <button onClick={restart} disabled={!session}>重启</button>
          <button onClick={forkBranch} disabled={!session || preview !== null}>
            ⑂ 派生分支
          </button>
          {session?.parentId && (
            <button onClick={abandonBranch}>放弃分支</button>
          )}
          {session && (
            <span className={`status status-${session.status}`}>
              {STATUS_LABEL[session.status]}
              {session.line != null && ` · 第 ${session.line} 行`}
              {session.parentId && ` · ${session.name}`}
            </span>
          )}
        </div>
      </header>

      {dirty && (
        <div className="banner">
          源程序已修改，尚未应用到调试会话。
          <button onClick={() => applySource(false)} disabled={preview !== null}>
            应用新程序（先预览断点迁移）
          </button>
          <button onClick={() => applySource(true)} disabled={preview !== null}>
            作为新分支试验
          </button>
        </div>
      )}
      {preview && (
        <div className="banner migration">
          <div className="migration-title">
            断点迁移预览{forkMode ? "（确认后创建实验分支，原现场保留）" : ""}
          </div>
          {preview.migrations.length === 0 && (
            <div className="migration-row">当前会话没有断点，切换后将从头开始。</div>
          )}
          {preview.migrations.map((item) => {
            const chosen = bpChoices[item.oldLine] ?? null;
            const reusable = item.status === "reusable";
            return (
              <div key={item.oldLine} className="migration-row">
                <label>
                  <input
                    type="checkbox"
                    checked={chosen !== null}
                    onChange={(e) =>
                      setBpChoices((prev) => ({
                        ...prev,
                        [item.oldLine]: e.target.checked
                          ? item.newLine ??
                              (item.candidates[0]?.line ?? null)
                          : null,
                      }))
                    }
                  />
                  <span className={reusable ? "tag-ok" : "tag-warn"}>
                    {reusable ? "可继续使用" : "需重新确认"}
                  </span>
                  第 {item.oldLine} 行
                  <code>{item.oldText || "（空行）"}</code>
                </label>
                {chosen !== null && item.candidates.length > 0 ? (
                  <select
                    value={chosen}
                    onChange={(e) =>
                      setBpChoices((prev) => ({
                        ...prev,
                        [item.oldLine]: Number(e.target.value),
                      }))
                    }
                  >
                    {item.candidates.map((c) => (
                      <option key={c.line} value={c.line}>
                        → 第 {c.line} 行：{c.text || "（空行）"}
                      </option>
                    ))}
                  </select>
                ) : (
                  chosen !== null && (
                    <span className="migration-target">
                      → 第 {chosen} 行
                    </span>
                  )
                )}
              </div>
            );
          })}
          <div className="migration-actions">
            <button className="primary" onClick={confirmSwitch}>
              {forkMode ? "确认为新分支" : "确认切换"}
            </button>
            <button onClick={cancelSwitch}>
              {forkMode ? "取消，不创建分支" : "取消，回到原会话"}
            </button>
          </div>
          <div className="migration-hint">
            确认前原程序的断点、调用栈与暂停位置保持不变；取消则完全回到原会话。
          </div>
        </div>
      )}
      {notice && <div className="banner notice">{notice}</div>}
      {error && <div className="banner error">{error}</div>}

      <main>
        <section className="editor">
          <div className="editor-inner">
            <div className="gutter">
              {lines.map((_, i) => {
                const ln = i + 1;
                const isBp = pendingLines
                  ? pendingLines.has(ln)
                  : session?.breakpoints.includes(ln);
                const isCurrent = session?.line === ln;
                return (
                  <div
                    key={ln}
                    className={`gutter-line ${isCurrent ? "current" : ""}`}
                    onClick={() => !preview && toggleBreakpoint(ln)}
                    title={preview ? "断点迁移预览中" : "点击切换断点"}
                  >
                    <span className={`bp ${isBp ? "on" : ""}`} />
                    {ln}
                  </div>
                );
              })}
            </div>
            <textarea
              value={source}
              onChange={(e) => setSource(e.target.value)}
              spellCheck={false}
              rows={Math.max(lines.length, 12)}
            />
          </div>
        </section>

        <aside>
          <section className="panel">
            <h2>分支</h2>
            {branches.length === 0 && <div className="empty">（无）</div>}
            {branches.map((b) => (
              <div
                key={b.id}
                className={`branch ${b.id === session?.id ? "selected" : ""}`}
                onClick={() => switchBranch(b.id)}
              >
                <span className="fname">
                  {b.name || "未命名"}
                  {b.id === session?.id ? "（当前）" : ""}
                </span>
                <span className="fline">
                  修订 {b.sourceHash.slice(0, 6)}
                  {b.parentId ? " · 实验分支" : " · 主分支"}
                </span>
              </div>
            ))}
            {session && session.bpMigrations.length > 0 && (
              <div className="migrations">
                <h3>已确认的断点迁移</h3>
                {session.bpMigrations.map((m, i) => (
                  <div key={i} className="migration-row">
                    第 {m.oldLine} 行 → 第 {m.newLine} 行
                    {m.fromHash && m.toHash && (
                      <span className="fline">
                        （{m.fromHash.slice(0, 6)} → {m.toHash.slice(0, 6)}）
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <h2>分支比较</h2>
            <div className="compare-controls">
              <select
                value={compareTarget}
                onChange={(e) => chooseCompareTarget(e.target.value)}
              >
                <option value="">选择对比分支…</option>
                {branches
                  .filter((b) => b.id !== session?.id)
                  .map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name || "未命名"}（{b.sourceHash.slice(0, 6)}）
                    </option>
                  ))}
              </select>
              <button onClick={runCompare} disabled={!session || !compareTarget}>
                比较
              </button>
              <button
                onClick={saveCompare}
                disabled={!session || !compareTarget}
                title="把当前比较保存为可重开的工作结果"
              >
                保存比较
              </button>
            </div>
            {compareTarget && (
              <div className="compare-boundaries">
                <label className="boundary-pick">
                  本侧暂停边界
                  <select
                    value={leftBoundary === undefined ? "" : String(leftBoundary)}
                    disabled={activeComparisonId !== ""}
                    onChange={(e) => {
                      setActiveComparisonId("");
                      setCompareLive(null);
                      setLeftBoundary(
                        e.target.value === ""
                          ? undefined
                          : Number(e.target.value)
                      );
                    }}
                  >
                    <option value="">当前暂停位置</option>
                    <option value="-1">会话开始（零事件）</option>
                    {leftBoundaries.map((b) => (
                      <option key={b.index} value={b.index}>
                        第 {b.line} 行（事件 {b.index}）
                      </option>
                    ))}
                  </select>
                </label>
                <label className="boundary-pick">
                  对侧暂停边界
                  <select
                    value={rightBoundary === undefined ? "" : String(rightBoundary)}
                    disabled={activeComparisonId !== ""}
                    onChange={(e) => {
                      setActiveComparisonId("");
                      setCompareLive(null);
                      setRightBoundary(
                        e.target.value === ""
                          ? undefined
                          : Number(e.target.value)
                      );
                    }}
                  >
                    <option value="">当前暂停位置</option>
                    <option value="-1">会话开始（零事件）</option>
                    {rightBoundaries.map((b) => (
                      <option key={b.index} value={b.index}>
                        第 {b.line} 行（事件 {b.index}）
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
            {savedComparisons.length > 0 && (
              <div className="saved-comparisons">
                <h3>已保存的比较</h3>
                {savedComparisons.map((c) => (
                  <div
                    key={c.id}
                    className={
                      "branch" + (c.id === activeComparisonId ? " selected" : "")
                    }
                    onClick={() => openComparison(c.id)}
                  >
                    <span className="fname">
                      {c.leftName} ↔ {c.rightName}
                    </span>
                    <span className="fline">
                      {COMPARE_STATUS_LABEL[c.status] ?? c.status}
                      {" · 边界 "}
                      {c.leftBoundary}/{c.rightBoundary}
                      {" · 修订 "}
                      {c.leftHash.slice(0, 6)}/{c.rightHash.slice(0, 6)}
                    </span>
                    <button
                      className="link-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        removeComparison(c.id);
                      }}
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>
            )}
            {compareResult && (
              <div className="compare-result">
                {activeComparisonId && (
                  <div className="saved-note">
                    已保存的比较记录：双方修订、边界与调用现场已钉住，
                    后续单步/继续/切换分支都不会改写它。
                    <button className="link-btn" onClick={backToLiveCompare}>
                      返回实时比较
                    </button>
                  </div>
                )}
                {compareLive &&
                  (["left", "right"] as const).map((side) => {
                    const liveSide = compareLive[side];
                    const pinned = compareResult[side];
                    if (liveSide.missing) {
                      return (
                        <div key={side} className="stale-note">
                          {pinned.name} 已被放弃，记录保留其当时的执行事实。
                        </div>
                      );
                    }
                    if (
                      liveSide.cursor !== undefined &&
                      liveSide.cursor !== pinned.cursor
                    ) {
                      return (
                        <div key={side} className="stale-note">
                          {pinned.name} 现已走到事件 {liveSide.cursor}
                          ，本记录仍钉在事件 {pinned.cursor}。
                        </div>
                      );
                    }
                    return null;
                  })}
                {compareResult.status === "identical" && (
                  <div className="empty">
                    两侧都已运行到结束，已执行部分的执行事实完全一致。
                  </div>
                )}
                {compareResult.status === "waiting" && (
                  <div className="empty">
                    已执行的公共部分一致；至少一侧尚未运行到所选位置，
                    后续部分标记为等待——不把尚未发生的未来行为当作事实。
                  </div>
                )}
                {compareResult.status === "ahead" && (
                  <div>
                    公共已执行部分一致；一侧走得更远（多执行了循环/递归步），
                    另一侧该位置尚未到达，并非输出或变量分歧。
                  </div>
                )}
                {compareResult.status === "diverged" && (
                  <>
                    <div>
                      首次不同：第 {compareResult.index} 个暂停步（
                      {REASON_LABEL[compareResult.reason ?? ""]}）
                    </div>
                    {!compareResult.sameRevision && (
                      <div className="revision-note">
                        两侧记录来自不同程序修订（
                        {compareResult.left.sourceHash.slice(0, 6)} vs{" "}
                        {compareResult.right.sourceHash.slice(0, 6)}
                        ），相同行号不代表同一步。
                      </div>
                    )}
                    {([compareResult.left, compareResult.right] as const).map(
                      (side, i) => (
                        <div key={i} className="compare-side">
                          <div className="fname">
                            {side.name} · 修订 {side.sourceHash.slice(0, 6)}
                          </div>
                          <div className="fline">
                            比较边界：事件 {side.boundary}
                            {side.boundary !== side.cursor && (
                              <span className="stale-note">
                                （该分支现已走到事件 {side.cursor}，本比较仍钉在旧边界）
                              </span>
                            )}
                            {' · '}
                            已执行 {side.executedCount} 个事件 / {side.stepCount} 个暂停步
                            {side.terminal ? " · 已到终态" : " · 仍在等待"}
                          </div>
                          {side.event ? (
                            <>
                              <div className="fline">
                                事件 {side.event.kind}
                                {side.event.line != null &&
                                  ` · 第 ${side.event.line} 行`}
                                {side.event.message &&
                                  ` · ${side.event.message}`}
                              </div>
                              {side.event.stack.length > 0 && (
                                <div className="fline">
                                  调用链：
                                  {side.event.stack
                                    .map((f, fi) => (
                                      <span key={fi} className="frame-chip">
                                        {f.name}@第{f.line}行
                                      </span>
                                    ))}
                                </div>
                              )}
                              {side.event.stack.length > 0 && (
                                <div className="fline">
                                  当前帧变量：
                                  {side.event.stack[
                                    side.event.stack.length - 1
                                  ].locals
                                    .map((v) => `${v.name}=${v.value.display}`)
                                    .join("，") || "（无局部变量）"}
                                </div>
                              )}
                              {side.event.output && (
                                <pre className="output">
                                  {side.event.output}
                                </pre>
                              )}
                            </>
                          ) : (
                            <div className="fline waiting-note">
                              （等待：该侧尚未执行到对应位置，未来事件不计入比较）
                            </div>
                          )}
                        </div>
                      )
                    )}
                  </>
                )}
              </div>
            )}
          </section>

          <section className="panel">
            <h2>调用栈</h2>
            {stack.length === 0 && <div className="empty">（尚未开始）</div>}
            {[...stack].reverse().map((f, ri) => {
              const idx = stack.length - 1 - ri;
              return (
                <div
                  key={f.uid}
                  className={`frame ${idx === frameIndex ? "selected" : ""}`}
                  onClick={() => setSelectedFrame(idx)}
                >
                  <span className="fname">{f.name}</span>
                  <span className="fline">第 {f.line} 行</span>
                </div>
              );
            })}
          </section>

          <section className="panel">
            <h2>变量{frame ? `（${frame.name}）` : ""}</h2>
            {!frame && <div className="empty">（无）</div>}
            {frame && frame.locals.length === 0 && (
              <div className="empty">（无局部变量）</div>
            )}
            {frame?.locals.map((v) => (
              <div key={v.name} className="var">
                <span className="vname">{v.name}</span>
                <span className={`vval t-${v.value.type}`}>{v.value.display}</span>
              </div>
            ))}
          </section>

          <section className="panel">
            <h2>输出</h2>
            <pre className="output">{session?.output || "（空）"}</pre>
          </section>

          {session?.error && (
            <section className="panel error-panel">
              <h2>错误</h2>
              <div className="err-msg">
                第 {session.error.line} 行：{session.error.message}
              </div>
              <h3>调用链</h3>
              {session.error.stack.map((f) => (
                <div key={f.uid} className="frame">
                  <span className="fname">{f.name}</span>
                  <span className="fline">第 {f.line} 行</span>
                </div>
              ))}
            </section>
          )}
        </aside>
      </main>
    </div>
  );
}
