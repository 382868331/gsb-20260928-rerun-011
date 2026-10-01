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


def test_forked_branches_keep_independent_pause_sites():
    base = DebugSession(RECURSIVE)
    base.set_breakpoints([2])
    base.continue_()  # fact(3), then fork before continuing into fact(2)
    assert locals_of(base.state())["n"] == "3"

    alpha = base.fork_branch("实验A")
    beta = base.fork_branch("实验B")
    assert alpha.workspace_id == beta.workspace_id == base.workspace_id
    assert alpha.forked_from == beta.forked_from == base.cursor

    alpha.set_breakpoints([2, 3])  # independent breakpoint set
    alpha.step_into()  # line 3: if n <= 1 in the same fact(3) frame
    assert alpha.state()["line"] == 3
    alpha.continue_()  # next reached breakpoint is line 2 inside fact(2)
    # Sibling's breakpoint set and pause position are untouched.
    assert beta.breakpoints == {2}
    assert beta.cursor == base.cursor
    assert locals_of(beta.state())["n"] == "3"

    beta.continue_()
    assert locals_of(beta.state())["n"] == "2"
    beta.continue_()
    assert locals_of(beta.state())["n"] == "1"
    # Switching back to alpha must not carry beta's deeper pause.
    assert locals_of(alpha.state())["n"] == "2"
    assert len(alpha.state()["stack"]) != len(beta.state()["stack"])


def test_failed_edit_and_abandon_do_not_hurt_parent_or_sibling(tmp_path):
    store = SessionStore(directory=tmp_path)
    base = store.create(LOOP)
    base.set_breakpoints([4])
    base.continue_()
    bad_branch = store.fork(base, "失败实验")
    good_branch = store.fork(base, "继续实验")

    with pytest.raises(SourceError):
        bad_branch.preview_source_change("let = \n")
    with pytest.raises(SourceError):
        bad_branch.commit_source_change("let = \n", [])
    store.abandon(bad_branch.id)

    assert store.get(base.id).cursor == base.cursor
    reopened_good = store.get(good_branch.id)
    assert reopened_good.state()["line"] == 4
    assert locals_of(reopened_good.state())["i"] == "0"
    reopened_good.continue_()
    assert locals_of(reopened_good.state())["i"] == "1"
    assert {b["id"] for b in store.workspace_branches(base.workspace_id)} == {
        base.id,
        good_branch.id,
    }


def test_all_unfinished_branches_restore_with_confirmed_migrations(tmp_path):
    store = SessionStore(directory=tmp_path)
    base = store.create(LOOP)
    base.set_breakpoints([3])
    base.continue_()
    edited = (
        "let i = 0\n"
        "let total = 0\n"
        "let note = 1\n"
        "while i < 5\n"
        "    total = total + i\n"
        "    i = i + 1\n"
        "print(total)\n"
    )
    preview = base.preview_source_change(edited)
    mappings = [
        {"oldLine": m["oldLine"], "newLine": m["newLine"]}
        for m in preview["migrations"]
        if m["status"] == "reusable"
    ]
    base.commit_source_change(edited, [m["newLine"] for m in mappings], mappings)
    store.touch(base)
    child = store.fork(base, "迁移检查")
    child.continue_()
    paused_line = child.state()["line"]
    store.touch(child)

    reopened_store = SessionStore(directory=tmp_path)
    root = reopened_store.get(base.id)
    branch = reopened_store.get(child.id)
    assert root.source == edited
    assert root.confirmed_migrations[0]["mappings"] == [{"oldLine": 3, "newLine": 4}]
    assert branch.confirmed_migrations == root.confirmed_migrations
    assert branch.state()["line"] == paused_line
    assert locals_of(branch.state())["i"] == "0"


def test_compare_identifies_first_variable_and_revision_difference():
    source_a = "let x = 1\nprint(x)\n"
    source_b = "let x = 2\nprint(x)\n"
    a = DebugSession(source_a)
    b = DebugSession(source_b)
    a.step_into()
    b.step_into()
    comparison = DebugSession.compare_facts(a, b)
    first = comparison["firstDifference"]
    assert first["difference"]["kind"] == "variable"
    assert first["difference"]["revisionChanged"] is True
    var_diff = first["difference"]["variables"][0]
    assert (var_diff["name"], var_diff["a"], var_diff["b"]) == ("x", "1", "2")

    a.step_into()
    b.step_into()
    comparison = DebugSession.compare_facts(a, b)
    # After the variable diverges, the subsequent print output differs too;
    # the report stops at the earliest variable difference.
    assert comparison["firstDifference"]["difference"]["kind"] == "variable"


def test_compare_output_difference_and_revision_line_warning():
    a = DebugSession("print(1)\n")
    b = DebugSession("print(2)\n")
    a.step_into()
    b.step_into()
    comparison = DebugSession.compare_facts(a, b)
    first = comparison["firstDifference"]
    assert first["difference"]["kind"] == "output"
    assert first["difference"]["lineNumberSame"] is True
    assert first["difference"]["revisionChanged"] is True
    assert "行号相同不代表同一步" in first["difference"]["message"]


def test_compare_same_revision_with_different_branch_length():
    a = DebugSession(LOOP)
    b = DebugSession(LOOP)
    a.step_into()
    a.step_into()
    b.step_into()
    comparison = DebugSession.compare_facts(a, b)
    assert comparison["firstDifference"]["difference"]["kind"] == "length"
    assert comparison["executedCounts"] == {"a": 2, "b": 1}
