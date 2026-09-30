import type { SessionState, SessionSummary, SourceCheck } from './types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body?.error || `请求失败 (${response.status})`;
    const error = new Error(message) as Error & {
      code?: string;
      line?: number;
    };
    error.code = body?.code;
    error.line = body?.line;
    throw error;
  }
  return body as T;
}

export const api = {
  listSessions: () =>
    request<{ sessions: SessionSummary[] }>('/api/sessions'),

  createSession: (source: string, name: string, breakpoints: number[]) =>
    request<SessionState>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ source, name, breakpoints }),
    }),

  getSession: (id: string) =>
    request<SessionState>(`/api/sessions/${id}`),

  setBreakpoints: (id: string, lines: number[]) =>
    request<SessionState>(`/api/sessions/${id}/breakpoints`, {
      method: 'PUT',
      body: JSON.stringify({ lines }),
    }),

  step: (id: string, kind: 'into' | 'over' | 'out') =>
    request<SessionState>(`/api/sessions/${id}/step/${kind}`, { method: 'POST' }),

  resume: (id: string) =>
    request<SessionState>(`/api/sessions/${id}/resume`, { method: 'POST' }),

  save: (id: string) =>
    request<{ saved: boolean }>(`/api/sessions/${id}/save`, { method: 'POST' }),

  remove: (id: string) =>
    request<{ deleted: boolean }>(`/api/sessions/${id}`, { method: 'DELETE' }),

  checkSource: (source: string) =>
    request<SourceCheck>('/api/source-check', {
      method: 'POST',
      body: JSON.stringify({ source }),
    }),
};
