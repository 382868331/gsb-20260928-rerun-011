export type ValueView = { type: string; display: string };

export type LocalView = { name: string; value: ValueView };

export type FrameView = {
  uid: number;
  name: string;
  line: number;
  locals: LocalView[];
};

export type EventView = {
  index: number;
  kind: 'line' | 'call' | 'return' | 'print' | 'error' | 'end' | 'stmt_end';
  line: number;
  level?: number;
  output: string;
  stack: FrameView[];
  text?: string;
  message?: string;
};

export type RejectedBreakpoint = { line: number; reason: string };

export type SessionState = {
  id: string;
  name: string;
  source: string;
  source_hash: string;
  status: 'paused' | 'ended' | 'error' | 'closed';
  breakpoints: number[];
  rejected_breakpoints: RejectedBreakpoint[];
  executable_lines: number[];
  cursor: number;
  event: EventView | null;
  error: {
    message: string;
    line: number;
    stack: FrameView[];
    output: string;
  } | null;
  event_count: number;
};

export type SessionSummary = {
  id: string;
  name: string;
  status: string;
  source_hash: string;
  cursor: number;
  event_count: number;
  breakpoints: number[];
  current_line?: number;
};

export type SourceCheck = {
  source_hash: string;
  matching_session_ids: string[];
  stale: { id: string; name: string }[];
  compiles?: boolean;
  compile_error?: { message: string; line?: number; code: string };
};
