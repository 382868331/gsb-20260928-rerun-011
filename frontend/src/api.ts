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
  workspaceId: string;
  branchName: string;
  parentId: string | null;
  forkedFrom: number | null;
  cursor?: number;
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
  confirmedMigrations?: MigrationRecord[];
}

export interface MigrationRecord {
  fromHash: string;
  toHash: string;
  mappings: { oldLine: number; newLine: number }[];
}

export interface BranchInfo {
  id: string;
  parentId: string | null;
  name: string;
  createdAt?: number;
  forkedFrom: number | null;
  sourceHash: string;
  cursor: number;
  breakpoints: number[];
  revisionPreview: string;
}

export interface FactFrame {
  depth: number;
  name: string;
  locals: Record<string, string>;
}

export interface ExecutionFact {
  seq: number;
  eventIndex: number;
  sourceHash: string;
  line: number;
  lineText: string;
  output: string;
  outputDelta: string;
  frames: FactFrame[];
  postOutput: string;
  postOutputDelta: string;
  postFrames: FactFrame[];
}

export interface FactDifference {
  kind: "variable" | "output" | "revision" | "length";
  message: string;
  revisionChanged?: boolean;
  lineNumberSame?: boolean;
  variables?: {
    frameDepth: number;
    frameName: string;
    kind: string;
    name?: string;
    a: string | null;
    b: string | null;
  }[];
  a?: string | null;
  b?: string | null;
}

export interface FactComparison {
  workspaceId: string;
  branches: {
    a: { id: string; name: string; sourceHash: string; status: string };
    b: { id: string; name: string; sourceHash: string; status: string };
  };
  firstDifference: {
    seq: number;
    a: ExecutionFact | null;
    b: ExecutionFact | null;
    difference: FactDifference;
  } | null;
  aligned: {
    seq: number;
    a: ExecutionFact | null;
    b: ExecutionFact | null;
    difference: FactDifference | null;
  }[];
  executedCounts: { a: number; b: number };
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
  commitSource: (
    id: string,
    source: string,
    breakpoints: number[],
    mappings: { oldLine: number; newLine: number }[]
  ) =>
    request<SessionState>(`/api/sessions/${id}/source/commit`, {
      method: "POST",
      body: JSON.stringify({ source, breakpoints, migrations: mappings }),
    }),
  branches: (id: string) =>
    request<{ workspaceId: string; branches: BranchInfo[] }>(
      `/api/sessions/${id}/branches`
    ),
  fork: (id: string, name: string) =>
    request<{ branch: SessionState; siblings: BranchInfo[] }>(
      `/api/sessions/${id}/branches`,
      {
        method: "POST",
        body: JSON.stringify({ name }),
      }
    ),
  abandon: (sessionId: string, branchId: string) =>
    request<{ workspaceId: string; branches: BranchInfo[] }>(
      `/api/sessions/${sessionId}/branches/${branchId}`,
      { method: "DELETE" }
    ),
  compare: (id: string, otherId: string) =>
    request<FactComparison>(`/api/sessions/${id}/compare`, {
      method: "POST",
      body: JSON.stringify({ otherId }),
    }),
};
