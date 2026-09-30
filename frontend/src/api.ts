export interface ValueJson {
  type: string;
  display: string;
}

export interface LocalVar {
  name: string;
  value: ValueJson;
}

export interface FrameSnapshot {
  uid: number;
  name: string;
  line: number;
  locals: LocalVar[];
}

export interface SessionState {
  id: string;
  status: "paused" | "finished" | "error";
  line: number | null;
  kind: string;
  output: string;
  stack: FrameSnapshot[];
  breakpoints: number[];
  source: string;
  sourceHash: string;
  error: { line: number; message: string; stack: FrameSnapshot[] } | null;
  sourceChanged?: boolean;
  invalidatedBreakpoints?: number[];
  migratedBreakpoints?: BreakpointMigration[];
}

export interface BreakpointMigration {
  from: number;
  to: number;
  text: string;
}

export interface ReconfirmBreakpoint {
  from: number;
  text: string;
  candidates: number[];
}

export interface SourcePreview {
  changed: boolean;
  source: string;
  source_hash: string;
  retained_breakpoints: BreakpointMigration[];
  reconfirm_breakpoints: ReconfirmBreakpoint[];
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败: ${res.status}`);
  }
  return res.json();
}

export const api = {
  create: (source: string) =>
    request<SessionState>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ source }),
    }),
  get: (id: string) => request<SessionState>(`/api/sessions/${id}`),
  step: (id: string, mode: "into" | "over") =>
    request<SessionState>(`/api/sessions/${id}/step`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  continue: (id: string) =>
    request<SessionState>(`/api/sessions/${id}/continue`, { method: "POST" }),
  restart: (id: string) =>
    request<SessionState>(`/api/sessions/${id}/restart`, { method: "POST" }),
  setBreakpoints: (id: string, lines: number[]) =>
    request<SessionState>(`/api/sessions/${id}/breakpoints`, {
      method: "PUT",
      body: JSON.stringify({ lines }),
    }),
  updateSource: (id: string, source: string) =>
    request<SessionState>(`/api/sessions/${id}/source`, {
      method: "PUT",
      body: JSON.stringify({ source }),
    }),
  previewSourceUpdate: (id: string, source: string) =>
    request<SourcePreview>(`/api/sessions/${id}/source/preview`, {
      method: "POST",
      body: JSON.stringify({ source }),
    }),
  commitSourceUpdate: (id: string, source: string, breakpoints: number[]) =>
    request<SessionState>(`/api/sessions/${id}/source/commit`, {
      method: "POST",
      body: JSON.stringify({ source, breakpoints }),
    }),
};
