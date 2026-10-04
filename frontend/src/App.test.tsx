import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import {
  CompareResult,
  LiveSide,
  SessionState,
} from "./api";

vi.mock("./api", () => ({
  api: {
    create: vi.fn(),
    get: vi.fn(),
    list: vi.fn(),
    remove: vi.fn(),
    fork: vi.fn(),
    pauseBoundaries: vi.fn(),
    compare: vi.fn(),
    saveCompare: vi.fn(),
    listComparisons: vi.fn(),
    getComparison: vi.fn(),
    deleteComparison: vi.fn(),
    step: vi.fn(),
    continue: vi.fn(),
    restart: vi.fn(),
    setBreakpoints: vi.fn(),
    previewSource: vi.fn(),
    commitSource: vi.fn(),
  },
}));

import { api } from "./api";
import App from "./App";

const mock = vi.mocked(api);

function makeSession(id: string, cursor: number): SessionState {
  return {
    id,
    name: id === "A" ? "主分支" : "实验分支",
    cursor,
    parentId: id === "B" ? "A" : null,
    forkCursor: id === "B" ? 0 : null,
    bpMigrations: [],
    status: "paused",
    line: 2,
    kind: "line",
    output: "",
    stack: [],
    breakpoints: [],
    source: "print(1)\n",
    sourceHash: `hash-${id}`,
    error: null,
  };
}

function makeCompare(cid: string, cursorA: number, cursorB: number): CompareResult {
  return {
    status: "waiting",
    reason: null,
    index: null,
    sameRevision: true,
    diverged: false,
    left: {
      id: "A",
      name: "主分支",
      sourceHash: "hash-A",
      boundary: cursorA,
      cursor: cursorA,
      executedCount: cursorA,
      stepCount: 10,
      terminal: false,
      event: null,
    },
    right: {
      id: "B",
      name: "实验分支",
      sourceHash: "hash-B",
      boundary: cursorB,
      cursor: cursorB,
      executedCount: cursorB,
      stepCount: 10,
      terminal: false,
      event: null,
    },
  };
}

function saved(cid: string, result: CompareResult, liveCursors: [number, number]) {
  const liveSide = (id: string, cursor: number): LiveSide => ({
    id,
    missing: false,
    name: id === "A" ? "主分支" : "实验分支",
    cursor,
    status: "paused",
  });
  return {
    record: {
      id: cid,
      created_at: 1,
      left_id: "A",
      right_id: "B",
      result,
    },
    live: {
      left: liveSide("A", liveCursors[0]),
      right: liveSide("B", liveCursors[1]),
    },
  };
}

const summaryZero = {
  id: "C0",
  created_at: 1,
  leftId: "A",
  rightId: "B",
  leftName: "主分支",
  rightName: "实验分支",
  status: "waiting",
  reason: null,
  index: null,
  leftBoundary: 0,
  rightBoundary: 0,
  leftHash: "hash-A",
  rightHash: "hash-B",
};

const summaryFour = {
  ...summaryZero,
  id: "C4",
  leftBoundary: 4,
  rightBoundary: 0,
};

function setupApi() {
  mock.list.mockResolvedValue({ sessions: [] });
  mock.listComparisons.mockResolvedValue({ comparisons: [] });
  mock.get.mockImplementation(async (id: string) => {
    if (id === "A") return makeSession("A", 0);
    if (id === "B") return makeSession("B", 0);
    throw new Error("not found");
  });
  mock.getComparison.mockImplementation(async (id: string) => {
    if (id === "C0") return saved("C0", makeCompare("C0", 0, 0), [4, 0]);
    if (id === "C4") return saved("C4", makeCompare("C4", 4, 0), [4, 0]);
    throw new Error("not found");
  });
  mock.step.mockImplementation(async (id: string) => makeSession(id, 4));
  mock.continue.mockImplementation(async (id: string) => makeSession(id, 4));
  mock.deleteComparison.mockResolvedValue({ ok: true });
}

beforeEach(() => {
  localStorage.clear();
  setupApi();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("saved comparison selection", () => {
  it("restores the viewed saved comparison after reload", async () => {
    localStorage.setItem("minilang.sessionId", "A");
    localStorage.setItem("minilang.comparisonId", "C4");
    const { findByText, unmount } = render(<App />);

    const note = await findByText(/已保存的比较记录/);
    expect(note).toBeTruthy();
    expect(localStorage.getItem("minilang.comparisonId")).toBe("C4");
    // The pinned record remains open even after effects run.
    await waitFor(() =>
      expect(localStorage.getItem("minilang.comparisonId")).toBe("C4")
    );

    unmount();

    const second = render(<App />);
    await second.findByText(/已保存的比较记录/);
    expect(localStorage.getItem("minilang.comparisonId")).toBe("C4");
    second.unmount();
  });

  it("keeps the saved record open while continuing", async () => {
    localStorage.setItem("minilang.sessionId", "A");
    localStorage.setItem("minilang.comparisonId", "C0");
    const { findByText, findAllByText } = render(<App />);
    await findByText(/已保存的比较记录/);

    const continueButtons = await findAllByText("继续");
    await act(async () => {
      fireEvent.click(continueButtons[0]);
    });

    await findByText(/主分支 现已走到事件 4/);
    expect(localStorage.getItem("minilang.comparisonId")).toBe("C0");
    // The pinned snapshot still shows its original zero-event boundary.
    await findByText(/本记录仍钉在事件 0/);
  });

  it("alternates between two saved records and restores each", async () => {
    mock.listComparisons.mockResolvedValue({
      comparisons: [summaryZero, summaryFour],
    });
    localStorage.setItem("minilang.sessionId", "A");
    localStorage.setItem("minilang.comparisonId", "C0");
    const { findByText, findAllByText, unmount } = render(<App />);
    await findByText(/已保存的比较记录/);

    // Open the second record (left side advanced four events).
    const entries = await findAllByText(/主分支 ↔ 实验分支/);
    await act(async () => {
      fireEvent.click(entries[1]);
    });
    await waitFor(() =>
      expect(localStorage.getItem("minilang.comparisonId")).toBe("C4")
    );

    unmount();
    const second = render(<App />);
    await second.findByText(/已保存的比较记录/);
    expect(localStorage.getItem("minilang.comparisonId")).toBe("C4");
    second.unmount();
  });

  it("clears the selection only when the user deletes the record", async () => {
    mock.listComparisons.mockResolvedValue({
      comparisons: [summaryFour],
    });
    localStorage.setItem("minilang.sessionId", "A");
    localStorage.setItem("minilang.comparisonId", "C4");
    const { findByText } = render(<App />);
    await findByText(/已保存的比较记录/);

    const del = await findByText("删除");
    await act(async () => {
      fireEvent.click(del);
    });

    await waitFor(() =>
      expect(localStorage.getItem("minilang.comparisonId")).toBe("")
    );
  });
});
