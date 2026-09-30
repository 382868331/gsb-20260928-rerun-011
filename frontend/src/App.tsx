import { useEffect, useMemo, useState } from "react";
import {
  api,
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

  const dirty = useMemo(
    () => session !== null && source !== session.source,
    [session, source]
  );

  useEffect(() => {
    // 每次暂停位置变化后，默认选中栈顶帧，保证变量跟随当前帧。
    setSelectedFrame(-1);
  }, [session?.line, session?.kind, session?.status]);

  async function run(action: () => Promise<SessionState>) {
    setError("");
    setNotice("");
    try {
      const state = await action();
      setSession(state);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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

  async function applySource() {
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
    try {
      const state = await api.commitSource(session.id, pendingSource, lines);
      setSession(state);
      setSource(state.source);
      setPreview(null);
      setPendingSource("");
      setBpChoices({});
      setNotice("已切换到新程序，断点迁移已应用，会话从头开始。");
      setError("");
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
          {session && (
            <span className={`status status-${session.status}`}>
              {STATUS_LABEL[session.status]}
              {session.line != null && ` · 第 ${session.line} 行`}
            </span>
          )}
        </div>
      </header>

      {dirty && (
        <div className="banner">
          源程序已修改，尚未应用到调试会话。
          <button onClick={applySource} disabled={preview !== null}>
            应用新程序（先预览断点迁移）
          </button>
        </div>
      )}
      {preview && (
        <div className="banner migration">
          <div className="migration-title">断点迁移预览</div>
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
              确认切换
            </button>
            <button onClick={cancelSwitch}>取消，回到原会话</button>
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
