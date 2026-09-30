from __future__ import annotations

import pytest

from backend.debugger import DebugSession, SessionStore, SourceError

RECURSIVE = """fun fact(n)
    let local = n * 10
    if n <= 1
        return 1
    return n * fact(n - 1)
let r = fact(3)
print(r)
"""

LOOP = """let i = 0
let total = 0
while i < 5
    total = total + i
    i = i + 1
print(total)
"""

BOOM = """fun explode(x)
    let y = 10 / x
    return y
fun wrapper(v)
    return explode(v)
let z = wrapper(0)
print(z)
"""


def locals_of(state, frame_index=-1):
    frame = state["stack"][frame_index]
    return {v["name"]: v["value"]["display"] for v in frame["locals"]}


def test_recursion_locals_follow_current_frame():
    s = DebugSession(RECURSIVE)
    s.set_breakpoints([2])  # let local = n * 10
    s.continue_()
    assert s.state()["line"] == 2
    # Outermost call: n == 3
    assert locals_of(s.state())["n"] == "3"
    s.continue_()
    # Deeper frame: same variable names, different values, top frame wins.
    top = locals_of(s.state())
    assert top["n"] == "2"
    assert len(s.state()["stack"]) == 3  # main + fact(3) + fact(2)
    s.continue_()
    top = locals_of(s.state())
    assert top["n"] == "1"
    # Older frames keep their own locals, not overwritten by deeper calls.
    frames = s.state()["stack"]
    assert {v["name"]: v["value"]["display"] for v in frames[-2]["locals"]}["n"] == "2"


def test_continue_does_not_show_stale_values():
    s = DebugSession(LOOP)
    s.set_breakpoints([4])  # total = total + i
    s.continue_()
    first = locals_of(s.state())
    assert first["i"] == "0"
    s.continue_()
    second = locals_of(s.state())
    assert second["i"] == "1"
    assert second["total"] == "0"
    s.continue_()
    third = locals_of(s.state())
    assert third["i"] == "2"
    assert third["total"] == "1"


def test_breakpoints_adjustable_while_paused():
    s = DebugSession(LOOP)
    s.set_breakpoints([4])
    s.continue_()
    assert s.state()["line"] == 4
    # Paused at line 4; move the breakpoint to line 5 instead.
    s.set_breakpoints([5])
    s.continue_()
    assert s.state()["line"] == 5
    # Paused before line 5 executes, so i is still 0.
    assert locals_of(s.state())["i"] == "0"


def test_step_over_skips_call_body():
    s = DebugSession(RECURSIVE)
    s.step_into()  # line 6: let r = fact(3)
    assert s.state()["line"] == 6
    s.step_over()
    assert s.state()["line"] == 7  # print(r), call body skipped
    assert locals_of(s.state())["r"] == "6"


def test_unparseable_edit_preserves_whole_session():
    s = DebugSession(LOOP)
    s.set_breakpoints([4, 5])
    s.continue_()
    assert s.state()["status"] == "paused"
    paused_line = s.state()["line"]
    paused_locals = locals_of(s.state())
    paused_stack_len = len(s.state()["stack"])
    paused_cursor = s.cursor

    # Repeated invalid submissions must never touch the live session.
    for bad in ("let = \n", "while \n", "fun ("):
        with pytest.raises(SourceError):
            s.preview_source_change(bad)
        with pytest.raises(SourceError):
            s.commit_source_change(bad, [])
        kept = s.state()
        assert kept["source"] == LOOP
        assert kept["breakpoints"] == [4, 5]
        assert kept["line"] == paused_line
        assert len(kept["stack"]) == paused_stack_len
        assert locals_of(kept) == paused_locals
        assert s.cursor == paused_cursor


def test_recursive_pause_edit_keeps_old_frames_until_commit():
    s = DebugSession(RECURSIVE)
    s.set_breakpoints([2])
    s.continue_()
    s.continue_()
    assert len(s.state()["stack"]) == 3
    top_n = locals_of(s.state())["n"]

    new_source = RECURSIVE.replace("n * 10", "n * 20")
    preview = s.preview_source_change(new_source)
    assert preview["changed"] is True
    # Preview never mutates: old recursive frames and current line remain.
    assert len(s.state()["stack"]) == 3
    assert locals_of(s.state())["n"] == top_n
    assert s.state()["breakpoints"] == [2]


def test_preview_marks_reusable_and_confirm_breakpoints():
    s = DebugSession(LOOP)
    s.set_breakpoints([3, 4])
    # Line 3 (while header) is unique and shifts by one -> reusable.
    # Line 4's statement text also exists at new line 5, and a second
    # identical statement is inserted -> ambiguous -> needs confirm.
    edited = (
        "let i = 0\n"
        "let total = 0\n"
        "let note = 1\n"
        "while i < 5\n"
        "    total = total + i\n"
        "    total = total + i\n"
        "    i = i + 1\n"
        "print(total)\n"
    )
    preview = s.preview_source_change(edited)
    by_old = {m["oldLine"]: m for m in preview["migrations"]}
    assert by_old[3]["status"] == "reusable"
    assert by_old[3]["newLine"] == 4
    assert by_old[4]["status"] == "confirm"
    assert by_old[4]["newLine"] is None
    assert {c["line"] for c in by_old[4]["candidates"]} == {5, 6}
    assert preview["executableLines"]


def test_cancel_switch_returns_to_original_session():
    s = DebugSession(LOOP)
    s.set_breakpoints([4])
    s.continue_()
    before = s.state()
    s.preview_source_change("print(1)\n")
    # "Cancel" = simply don't commit; session state is byte-for-byte usable.
    s.continue_()
    after = s.state()
    assert after["line"] == 4
    assert locals_of(after)["i"] == "1"
    assert after["breakpoints"] == before["breakpoints"]


def test_commit_resets_trace_with_confirmed_breakpoints():
    s = DebugSession(LOOP)
    s.set_breakpoints([4])
    s.continue_()
    assert s.state()["status"] == "paused"

    new_source = "print(1)\nprint(2)\n"
    s.commit_source_change(new_source, [])
    state = s.state()
    assert state["source"] == new_source
    assert state["line"] is None
    assert state["stack"] == []
    assert state["breakpoints"] == []
    assert state["status"] == "paused"
    # The new trace is live and never carries old frames.
    s.continue_()
    assert s.state()["status"] == "finished"
    assert s.state()["output"] == "1\n2\n"


def test_switch_after_save_and_reopen(tmp_path):
    store = SessionStore(directory=tmp_path)
    s = store.create(RECURSIVE)
    s.set_breakpoints([2])
    s.continue_()
    s.continue_()
    store.touch(s)
    old_line = s.state()["line"]
    assert len(s.state()["stack"]) == 3

    reopened = SessionStore(directory=tmp_path).get(s.id)
    assert reopened.state()["line"] == old_line
    new_source = RECURSIVE.replace("fact", "fct")
    preview = reopened.preview_source_change(new_source)
    assert preview["changed"] is True
    migrated = [m["newLine"] for m in preview["migrations"] if m["status"] == "reusable"]
    reopened.commit_source_change(new_source, migrated)
    store.touch(reopened)
    state = reopened.state()
    assert state["line"] is None
    assert state["stack"] == []
    # The newly reopened record belongs entirely to the new program.
    again = SessionStore(directory=tmp_path).get(s.id)
    assert again.state()["source"] == new_source
    assert again.state()["stack"] == []
    again.continue_()
    assert again.state()["line"] == 2


def test_save_and_reopen_restores_pause(tmp_path):
    store = SessionStore(directory=tmp_path)
    s = store.create(RECURSIVE)
    s.set_breakpoints([2])
    s.continue_()
    s.continue_()
    store.touch(s)
    paused_line = s.state()["line"]
    paused_locals = locals_of(s.state())

    fresh = SessionStore(directory=tmp_path)
    reopened = fresh.get(s.id)
    assert reopened.state()["line"] == paused_line
    assert locals_of(reopened.state()) == paused_locals
    assert reopened.state()["breakpoints"] == [2]
    # Can keep debugging after reopen.
    reopened.continue_()
    assert reopened.state()["line"] == 2
    assert locals_of(reopened.state())["n"] == "1"


def test_error_state_keeps_call_chain_after_exit():
    s = DebugSession(BOOM)
    s.continue_()  # no breakpoints -> runs to the error
    state = s.state()
    assert state["status"] == "error"
    assert "除以零" in state["error"]["message"]
    names = [f["name"] for f in state["error"]["stack"]]
    assert names == ["main", "wrapper", "explode"]
    # Stepping/continuing after exit must not move or clear the error.
    s.step_into()
    s.continue_()
    again = s.state()
    assert again["status"] == "error"
    assert [f["name"] for f in again["error"]["stack"]] == names


def test_loop_runs_to_clean_finish():
    s = DebugSession(LOOP)
    s.continue_()
    state = s.state()
    assert state["status"] == "finished"
    assert state["output"] == "10\n"


def test_bad_source_rejected():
    with pytest.raises(SourceError):
        DebugSession("let = \n")
