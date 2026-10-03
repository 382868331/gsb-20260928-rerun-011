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
  cursor: number;
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
  cursor: number;
  sourceHash: string;
  preview: string[];
}

export interface PauseBoundary {
  index: number;
  line: number | null;
  kind: string;
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
  boundary: number;
  cursor: number;
  executedCount: number;
  stepCount: number;
  terminal: boolean;
  event: CompareEvent | null;
}

export interface CompareResult {
  status: "identical" | "waiting" | "ahead" | "diverged";
  diverged: boolean;
  index: number | null;
  reason: "variables" | "output" | "kind" | "ahead" | null;
  common: number;
  sameRevision: boolean;
  left: CompareSide;
  right: CompareSide;
}

export interface SavedComparisonSummary {
  id: string;
  created_at: number | null;
  name: string | null;
  leftId: string;
  rightId: string;
  leftBoundary: number;
  rightBoundary: number;
  status: CompareResult["status"] | null;
  leftName: string | null;
  rightName: string | null;
  leftHash: string | null;
  rightHash: string | null;
}

export interface SavedComparison {
  id: string;
  created_at: number;
  name: string;
  left_id: string;
  right_id: string;
  left_boundary: number;
  right_boundary: number;
  result: CompareResult;
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
  pauseBoundaries: (id: string) =>
    request<{ id: string; cursor: number; boundaries: PauseBoundary[] }>(
      `/api/sessions/${id}/pause-boundaries`
    ),
  compare: (
    id: string,
    otherId: string,
    boundaries?: { leftBoundary?: number; rightBoundary?: number }
  ) => {
    const params = new URLSearchParams();
    if (boundaries?.leftBoundary !== undefined)
      params.set("leftBoundary", String(boundaries.leftBoundary));
    if (boundaries?.rightBoundary !== undefined)
      params.set("rightBoundary", String(boundaries.rightBoundary));
    const query = params.toString();
    return request<CompareResult>(
      `/api/sessions/${id}/compare/${otherId}${query ? `?${query}` : ""}`
    );
  },
  saveCompare: (
    id: string,
    otherId: string,
    body: { leftBoundary?: number; rightBoundary?: number; name?: string }
  ) =>
    request<SavedComparison>(`/api/sessions/${id}/compare/${otherId}/save`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listComparisons: () =>
    request<{ comparisons: SavedComparisonSummary[] }>("/api/comparisons"),
  getComparison: (id: string) =>
    request<SavedComparison>(`/api/comparisons/${id}`),
  deleteComparison: (id: string) =>
    request<{ ok: boolean }>(`/api/comparisons/${id}`, { method: "DELETE" }),
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
