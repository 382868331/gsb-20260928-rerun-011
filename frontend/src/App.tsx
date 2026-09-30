import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { EXAMPLES } from './examples';
import { SourceEditor } from './components/SourceEditor';
import { StackPanel } from './components/StackPanel';
import { VariablesPanel } from './components/VariablesPanel';
import { SessionsPanel } from './components/SessionsPanel';
import type { FrameView, SessionState, SessionSummary } from './types';

const STATUS_TEXT: Record<string, string> = {
  paused: '暂停中',
  ended: '程序已正常退出',
  error: '程序因运行错误终止',
  closed: '会话已关闭',
};

export default function App() {
  const [draft, setDraft] = useState(EXAMPLES.recursion.source);
  const [session, setSession] = useState<SessionState | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedUid, setSelectedUid] = useState<number | null>(null);
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [compileInfo, setCompileInfo] = useState<string | null>(null);

  const refreshList = useCallback(async () => {
    try {
      const result = await api.listSessions();
      setSessions(result.sessions);
    } catch {
      // Listing is best-effort; the main flow surfaces its own errors.
    }
  }, []);

  useEffect(() => {
    refreshList();
  }, [refreshList]);

  // Every new pause position resets the inspected frame to the top frame,
  // so stale locals from a previous pause can never stay on screen.
  useEffect(() => {
    const frames = session?.event?.stack ?? session?.error?.stack ?? [];
    setSelectedUid(frames.length ? frames[frames.length - 1].uid : null);
  }, [session?.cursor, session?.status]);

  const applyState = useCallback((state: SessionState) => {
    setSession(state);
    refreshList();
  }, [refreshList]);

  const flash = useCallback((kind: 'info' | 'error', text: string) => {
    setMessage({ kind, text });
  }, []);

  const runAction = useCallback(async (action: () => Promise<SessionState>) => {
    setBusy(true);
    try {
      applyState(await action());
    } catch (error) {
      flash('error', (error as Error).message);
    } finally {
      setBusy(false);
    }
  }, [applyState, flash]);

  const activeFrames: FrameView[] = useMemo(() => {
    if (!session) return [];
    if (session.status === 'error' && session.error) {
      return session.error.stack;
    }
    return session.event?.stack ?? [];
  }, [session]);

  const selectedFrame = useMemo(() => {
    return activeFrames.find((frame) => frame.uid === selectedUid) ?? null;
  }, [activeFrames, selectedUid]);

  const currentLine = session?.event?.line ?? session?.error?.line ?? null;
  const frameLines = useMemo(
    () => activeFrames.map((frame) => frame.line),
    [activeFrames],
  );

  const isPaused = session?.status === 'paused';

  const handleCheck = async () => {
    setBusy(true);
    try {
      const result = await api.checkSource(draft);
      if (result.compiles) {
        const staleNote = result.stale.length
          ? `；另有 ${result.stale.length} 个旧会话属于不同源程序，不会沿用其行号断点`
          : '';
        setCompileInfo(`编译通过${staleNote}。`);
        flash('info', '编译通过，可以开始调试');
      } else {
        const err = result.compile_error;
        setCompileInfo(null);
        flash('error', `编译失败：${err?.message || '未知错误'}`);
      }
    } catch (error) {
      flash('error', (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleStart = async () => {
    setBusy(true);
    try {
      // A new program starts with an empty breakpoint set; old line numbers
      // are never copied from the previous program.
      const state = await api.createSession(draft, '调试会话', []);
      setCompileInfo(null);
      applyState(state);
      flash('info', '调试已开始，程序停在第一条可执行语句');
    } catch (error) {
      flash('error', (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleToggleBreakpoint = async (line: number) => {
    if (!session) return;
    const current = new Set(session.breakpoints);
    if (current.has(line)) {
      current.delete(line);
    } else {
      current.add(line);
    }
    setBusy(true);
    try {
      const state = await api.setBreakpoints(session.id, [...current]);
      applyState(state);
      const rejected = state.rejected_breakpoints.find((item) => item.line === line);
      if (rejected) {
        flash('error', `第 ${line} 行：${rejected.reason}`);
      }
    } catch (error) {
      flash('error', (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleResume = () => session && runAction(() => api.resume(session.id));
  const handleStep = (kind: 'into' | 'over' | 'out') =>
    session && runAction(() => api.step(session.id, kind));

  const handleSave = async () => {
    if (!session) return;
    setBusy(true);
    try {
      await api.save(session.id);
      await refreshList();
      flash('info', `会话 ${session.id} 已保存，关闭页面后可从“已保存会话”重开并继续`);
    } catch (error) {
      flash('error', (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleReopen = async (id: string) => {
    setBusy(true);
    try {
      const state = await api.getSession(id);
      setDraft(state.source);
      applyState(state);
      flash('info', `已重新打开会话（${STATUS_TEXT[state.status]}），可查看状态${state.status === 'paused' ? '并继续执行' : ''}`);
    } catch (error) {
      flash('error', (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.remove(id);
      if (session?.id === id) {
        setSession(null);
      }
      refreshList();
    } catch (error) {
      flash('error', (error as Error).message);
    }
  };

  const handleEditNew = () => {
    // Old session is left intact and persisted; editing simply detaches it.
    // Breakpoints shown in the editor are reset (new program, new lines).
    setSession(null);
    setSelectedUid(null);
    setCompileInfo(null);
    flash('info', '已离开旧会话。旧断点只属于旧源程序，不会套用到正在编辑的新程序上。');
  };

  const output = session?.error?.output ?? session?.event?.output ?? '';

  return (
    <div className="app">
      <header className="app-header">
        <h1>MiniLang 程序执行可视化调试器</h1>
        <div className="examples">
          <span>示例：</span>
          {Object.entries(EXAMPLES).map(([key, example]) => (
            <button
              key={key}
              type="button"
              disabled={!!session}
              onClick={() => setDraft(example.source)}
            >
              {example.label}
            </button>
          ))}
        </div>
      </header>

      {message && (
        <div className={`message ${message.kind}`}>
          <span>{message.text}</span>
          <button type="button" onClick={() => setMessage(null)}>×</button>
        </div>
      )}

      <div className="layout">
        <section className="panel editor-panel">
          <div className="panel-title">
            <span>源代码</span>
            <div className="editor-actions">
              {!session && (
                <>
                  <button type="button" disabled={busy} onClick={handleCheck}>编译检查</button>
                  <button type="button" className="primary" disabled={busy} onClick={handleStart}>
                    开始调试
                  </button>
                </>
              )}
              {session && (
                <button type="button" disabled={busy} onClick={handleEditNew}>
                  结束并编辑新程序
                </button>
              )}
            </div>
          </div>
          {compileInfo && <div className="compile-info">{compileInfo}</div>}
          <SourceEditor
            source={session ? session.source : draft}
            currentLine={currentLine}
            breakpoints={session?.breakpoints ?? []}
            executableLines={session?.executable_lines ?? []}
            frameLines={frameLines}
            readOnly={!!session}
            onSourceChange={setDraft}
            onToggleBreakpoint={handleToggleBreakpoint}
          />
        </section>

        <section className="panel side-panel">
          <div className="panel-title">
            <span>调试控制</span>
            {session && (
              <span className={`status-badge status-${session.status}`}>
                {STATUS_TEXT[session.status]}
              </span>
            )}
          </div>
          <div className="controls">
            <button type="button" disabled={busy || !isPaused} onClick={() => handleStep('into')}>
              单步进入
            </button>
            <button type="button" disabled={busy || !isPaused} onClick={() => handleStep('over')}>
              单步越过
            </button>
            <button type="button" disabled={busy || !isPaused} onClick={() => handleStep('out')}>
              跳出函数
            </button>
            <button type="button" className="primary" disabled={busy || !isPaused}
              title={session.breakpoints.length === 0 ? '没有断点时将一直运行到程序结束' : undefined}
              onClick={handleResume}>
              {session.breakpoints.length === 0 ? '继续到结束' : '继续到断点'}
            </button>
            <button type="button" disabled={busy || !session} onClick={handleSave}>保存会话</button>
          </div>
          {session && isPaused && (
            <div className="hint">
              在左侧行号旁的圆圈点击即可设置断点，暂停时也可以增删断点；
              {session.breakpoints.length === 0 ? '当前没有断点，继续会运行到程序结束。' : ''}
            </div>
          )}

          <div className="panel-title subsection">调用栈</div>
          <StackPanel
            frames={activeFrames}
            selectedUid={selectedUid}
            onSelect={setSelectedUid}
          />

          <div className="panel-title subsection">变量（跟随所选栈帧）</div>
          <VariablesPanel frame={selectedFrame} />
        </section>
      </div>

      <div className="layout bottom">
        <section className="panel">
          <div className="panel-title">程序输出</div>
          <pre className="output">{output || '（暂无输出）'}</pre>
        </section>
        <section className="panel">
          <div className="panel-title">最后一次错误与调用链</div>
          {session?.error ? (
            <div className="error-box">
              <div className="error-line">第 {session.error.line} 行：{session.error.message}</div>
              <ol className="error-stack">
                {[...session.error.stack].reverse().map((frame) => (
                  <li key={frame.uid}>
                    {frame.name}（第 {frame.line} 行）
                    {frame.locals.length > 0 && (
                      <span className="error-locals">
                        {' {'}
                        {frame.locals
                          .map((local) => `${local.name}=${local.value.display}`)
                          .join(', ')}
                        {'}'}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
              <div className="hint">程序已退出，以上信息保留可查，也可保存后重开查看。</div>
            </div>
          ) : (
            <div className="empty-hint">
              {session?.status === 'ended' ? '程序正常退出，没有错误。' : '尚无运行错误。'}
            </div>
          )}
        </section>
        <section className="panel">
          <div className="panel-title">已保存会话（暂停 / 结束 / 出错均可重开）</div>
          <SessionsPanel
            sessions={sessions}
            activeId={session?.id ?? null}
            onOpen={handleReopen}
            onDelete={handleDelete}
          />
        </section>
      </div>
    </div>
  );
}
