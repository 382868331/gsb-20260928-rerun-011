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
  name: string;
  parentId: string | null;
  forkCursor: number | null;
  bpMigrations: BpMigration[];
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
  migration?: MigrationPreview | null;
}

export interface BpMigration {
  oldLine: number;
  newLine: number;
  fromHash?: string;
  toHash?: string;
}

export interface BranchInfo {
  id: string;
  name: string | null;
  parentId: string | null;
  created_at: number | null;
  sourceHash: string;
  preview: string[];
}

export interface CompareEvent {
  index: number;
  kind: string;
  line: number | null;
  sourceHash: string;
  output: string;
  stack: FrameSnapshot[];
  message: string | null;
}

export interface CompareSide {
  id: string;
  name: string;
  sourceHash: string;
  event: CompareEvent | null;
  boundary: number | null;
  executedCount: number;
  waiting: boolean;
}

export interface BoundaryState {
  id: string;
  name: string;
  sourceHash: string;
  boundary: number;
  line: number | null;
  kind: string;
  output: string;
  stack: FrameSnapshot[];
}

export interface CompareSummary {
  id: string;
  createdAt: number;
  leftId: string;
  rightId: string;
  status: "identical" | "waiting" | "diverged";
  reason: "variables" | "output" | "kind" | "ahead" | null;
  index: number | null;
  sameRevision: boolean;
  leftBoundary: number;
  rightBoundary: number;
}

export interface CompareRecord {
  id: string;
  createdAt: number;
  leftId: string;
  rightId: string;
  result: CompareResult;
  leftState: BoundaryState;
  rightState: BoundaryState;
}

export interface CompareResult {
  diverged: boolean;
  status: "identical" | "waiting" | "diverged";
  index: number | null;
  reason: "variables" | "output" | "kind" | "ahead" | null;
  sameRevision: boolean;
  left: CompareSide;
  right: CompareSide;
  ahead: "left" | "right" | null;
  aheadEvents: CompareEvent[];
}

export interface MigrationCandidate {
  line: number;
  text: string;
}

export interface MigrationItem {
  oldLine: number;
  oldText: string;
  status: "reusable" | "confirm";
  newLine: number | null;
  candidates: MigrationCandidate[];
}

export interface MigrationPreview {
  changed: boolean;
  source?: string;
  newSourceHash?: string;
  executableLines?: number[];
  migrations: MigrationItem[];
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
  list: () => request<{ sessions: BranchInfo[] }>("/api/sessions"),
  remove: (id: string) =>
    request<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),
  fork: (
    id: string,
    body: {
      source?: string;
      name?: string;
      breakpoints?: number[];
      bpMigrations?: BpMigration[];
    }
  ) =>
    request<SessionState>(`/api/sessions/${id}/fork`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  compare: (id: string, otherId: string) =>
    request<CompareResult>(`/api/sessions/${id}/compare/${otherId}`),
  compareSave: (
    id: string,
    otherId: string,
    body: { leftCursor?: number; rightCursor?: number }
  ) =>
    request<CompareRecord>(`/api/sessions/${id}/compare/${otherId}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  comparisons: (sessionId?: string) =>
    request<{ comparisons: CompareSummary[] }>(
      `/api/comparisons${sessionId ? `?session=${sessionId}` : ""}`
    ),
  comparison: (recordId: string) =>
    request<CompareRecord>(`/api/comparisons/${recordId}`),
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
  previewSource: (id: string, source: string) =>
    request<SessionState>(`/api/sessions/${id}/source/preview`, {
      method: "POST",
      body: JSON.stringify({ source }),
    }),
  commitSource: (id: string, source: string, breakpoints: number[]) =>
    request<SessionState>(`/api/sessions/${id}/source/commit`, {
      method: "POST",
      body: JSON.stringify({ source, breakpoints }),
    }),
};
