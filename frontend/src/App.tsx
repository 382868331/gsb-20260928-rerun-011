import { useEffect, useMemo, useState } from "react";
import { api, SessionState } from "./api";

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
      if (state.invalidatedBreakpoints && state.invalidatedBreakpoints.length > 0) {
        setNotice(
          `源程序已变化，旧断点（第 ${state.invalidatedBreakpoints.join(
            "、"
          )} 行）已失效并清除，请重新设置。`
        );
      } else if (state.sourceChanged) {
        setNotice("源程序已变化，旧会话位置已重置。");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const start = () => run(() => api.create(source));
  const applySource = () => session && run(() => api.updateSource(session.id, source));
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

  const lines = source.split("\n");
  const stack = session?.stack ?? [];
  const frameIndex =
    selectedFrame >= 0 && selectedFrame < stack.length
      ? selectedFrame
      : stack.length - 1;
  const frame = frameIndex >= 0 ? stack[frameIndex] : null;
  const paused = session?.status === "paused";

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
          <button onClick={applySource}>应用新程序（旧断点将失效）</button>
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
                const isBp = session?.breakpoints.includes(ln);
                const isCurrent = session?.line === ln;
                return (
                  <div
                    key={ln}
                    className={`gutter-line ${isCurrent ? "current" : ""}`}
                    onClick={() => toggleBreakpoint(ln)}
                    title="点击切换断点"
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
