import type { SessionSummary } from '../types';

type Props = {
  sessions: SessionSummary[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
};

const STATUS_LABEL: Record<string, string> = {
  paused: '已暂停',
  ended: '已结束',
  error: '运行错误',
  closed: '已关闭',
};

export function SessionsPanel({ sessions, activeId, onOpen, onDelete }: Props) {
  if (!sessions.length) {
    return <div className="empty-hint">还没有保存的会话。</div>;
  }
  return (
    <ul className="sessions-list">
      {sessions.map((session) => (
        <li
          key={session.id}
          className={session.id === activeId ? 'session active' : 'session'}
        >
          <button
            type="button"
            className="session-open"
            onClick={() => onOpen(session.id)}
            title="重新打开该会话"
          >
            <span className="session-name">{session.name}</span>
            <span className={`session-status status-${session.status}`}>
              {STATUS_LABEL[session.status] || session.status}
            </span>
            <span className="session-line">
              {session.current_line ? `第 ${session.current_line} 行` : '—'}
            </span>
          </button>
          <button
            type="button"
            className="session-delete"
            title="删除该会话"
            onClick={() => onDelete(session.id)}
          >
            删除
          </button>
        </li>
      ))}
    </ul>
  );
}
