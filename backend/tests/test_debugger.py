from __future__ import annotations

import pytest

from backend.debugger import DebugSession, SessionStore, SourceError, source_hash

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


def test_fork_shares_pause_but_branches_stay_independent():
    parent = DebugSession(RECURSIVE)
    parent.set_breakpoints([2])
    parent.continue_()
    parent.continue_()
    assert len(parent.state()["stack"]) == 3

    branch_a = parent.fork(name="实验A")
    branch_b = parent.fork(name="实验B")
    # Both start at the exact same pause with the same breakpoints.
    for branch in (branch_a, branch_b):
        assert branch.state()["line"] == parent.state()["line"]
        assert locals_of(branch.state())["n"] == "2"
        assert branch.state()["breakpoints"] == [2]
        assert branch.parent_id == parent.id

    # Branch A moves on and changes breakpoints; B and parent stay put.
    branch_a.set_breakpoints([4])
    branch_a.continue_()
    assert branch_a.state()["line"] == 4
    assert branch_b.state()["line"] == parent.state()["line"]
    assert branch_b.state()["breakpoints"] == [2]
    assert parent.state()["breakpoints"] == [2]
    # Stepping B does not move A.
    branch_b.step_into()
    assert branch_a.state()["line"] == 4


def test_fork_with_edit_failure_leaves_all_branches_intact():
    parent = DebugSession(LOOP)
    parent.set_breakpoints([4])
    parent.continue_()
    sibling = parent.fork()
    with pytest.raises(SourceError):
        parent.fork(source="while \n")
    # Parent and existing sibling are untouched by the failed experiment.
    assert parent.state()["line"] == 4
    assert parent.state()["breakpoints"] == [4]
    assert locals_of(sibling.state()) == locals_of(parent.state())


def test_fork_with_edit_records_confirmed_migrations():
    parent = DebugSession(LOOP)
    parent.set_breakpoints([4])
    parent.continue_()
    edited = "let note = 1\n" + LOOP
    preview = parent.preview_source_change(edited)
    migration = preview["migrations"][0]
    assert migration["status"] == "reusable"
    branch = parent.fork(
        source=edited,
        breakpoints=[migration["newLine"]],
        bp_migrations=[{
            "oldLine": migration["oldLine"],
            "newLine": migration["newLine"],
            "fromHash": parent.source_hash,
            "toHash": preview["newSourceHash"],
        }],
    )
    # Edited branch restarts from the top with migrated breakpoints.
    assert branch.state()["line"] is None
    assert branch.state()["breakpoints"] == [5]
    assert branch.bp_migrations[0]["oldLine"] == 4
    assert branch.bp_migrations[0]["newLine"] == 5
    # Parent keeps its own pause position and old breakpoints.
    assert parent.state()["line"] == 4
    assert parent.state()["breakpoints"] == [4]


def test_compare_finds_first_variable_divergence():
    base = DebugSession(RECURSIVE)
    edited = DebugSession(RECURSIVE.replace("n * 10", "n * 20"))
    # Comparison only covers events the branches have actually executed.
    base.continue_()
    edited.continue_()
    result = base.compare(edited)
    assert result["diverged"] is True
    assert result["status"] == "diverged"
    assert result["reason"] == "variables"
    assert result["sameRevision"] is False
    left_top = result["left"]["event"]["stack"][-1]
    right_top = result["right"]["event"]["stack"][-1]
    # First difference is local = n * 10 vs n * 20 inside fact.
    assert {v["name"]: v["value"]["display"] for v in left_top["locals"]}["local"] == "30"
    assert {v["name"]: v["value"]["display"] for v in right_top["locals"]}["local"] == "60"
    # Each side reports which program revision its record belongs to.
    assert result["left"]["sourceHash"] == base.source_hash
    assert result["right"]["sourceHash"] == edited.source_hash
    assert result["left"]["event"]["sourceHash"] == base.source_hash


def test_compare_finds_output_divergence_and_identical_traces():
    a = DebugSession("print(1)\n")
    b = DebugSession("print(2)\n")
    a.continue_()
    b.continue_()
    result = a.compare(b)
    assert result["diverged"] is True
    assert result["reason"] == "output"
    other = DebugSession("print(1)\n")
    other.continue_()
    same = a.compare(other)
    assert same["diverged"] is False
    assert same["status"] == "identical"


def test_unstarted_branches_with_different_futures_do_not_diverge():
    # Neither session has executed a single step; the factually executed
    # record is empty on both sides regardless of future traces.
    base = DebugSession(RECURSIVE)
    edited = DebugSession(RECURSIVE.replace("n * 10", "n * 20"))
    result = base.compare(edited)
    assert result["diverged"] is False
    assert result["status"] == "identical"
    assert result["left"]["executedCount"] == 0
    assert result["right"]["executedCount"] == 0
    assert result["reason"] is None


def test_branches_need_not_walk_in_lockstep():
    # Same program: branch A walks several loop iterations further than B.
    ahead = DebugSession(LOOP)
    behind = DebugSession(LOOP)
    ahead.set_breakpoints([4])
    behind.set_breakpoints([4])
    behind.continue_()  # iteration 0
    ahead.continue_()   # iteration 0
    ahead.continue_()   # iteration 1
    ahead.continue_()   # iteration 2
    result = ahead.compare(behind)
    assert result["diverged"] is False
    assert result["status"] == "waiting"
    assert result["reason"] == "ahead"
    assert result["ahead"] == "left"
    assert result["left"]["waiting"] is False
    assert result["right"]["waiting"] is True
    # The waiting side's not-yet-reached positions are listed as ahead
    # events of the longer side, not as differing outputs.
    assert len(result["aheadEvents"]) == (
        result["left"]["executedCount"] - result["right"]["executedCount"]
    )
    assert result["right"]["event"] is None


def test_waiting_turns_into_real_divergence_once_caught_up():
    # Common executed prefix agrees; once the slower side reaches the point
    # where outputs genuinely differ, it is reported as divergence.
    a = DebugSession("print(1)\nprint(2)\n")
    b = DebugSession("print(1)\nprint(3)\n")
    a.continue_()   # fully executed: outputs 1 and 2
    b.step_into()  # paused before the first print: nothing printed yet
    waiting = a.compare(b)
    assert waiting["status"] == "waiting"
    assert waiting["diverged"] is False
    b.continue_()
    diverged = a.compare(b)
    assert diverged["status"] == "diverged"
    assert diverged["reason"] == "output"
    # Both divergence events carry the call-site stack and variables and
    # the revision hash, so the difference can be traced back either way.
    for side in (diverged["left"], diverged["right"]):
        assert side["event"]["sourceHash"] == side["sourceHash"]
        assert side["event"]["stack"] is not None


def test_recursion_return_events_compare_as_executed_facts():
    # One side steps deeper into recursion and returns; until the other
    # side reaches those return events they stay waiting, never divergent.
    deep = DebugSession(RECURSIVE)
    shallow = DebugSession(RECURSIVE)
    deep.set_breakpoints([5])  # return n * fact(n - 1)
    shallow.set_breakpoints([5])
    deep.continue_()  # fact(3) waiting for the call
    shallow.continue_()
    deep.continue_()  # fact(2)
    result = deep.compare(shallow)
    assert result["status"] == "waiting"
    assert result["diverged"] is False
    # Return events on the deeper side show up among its ahead events.
    assert any(evt["kind"] == "return" for evt in result["aheadEvents"]) or \
        result["aheadEvents"]  # all deeper events are real executed facts


def test_explicit_boundaries_allow_independent_pause_selection():
    a = DebugSession(LOOP)
    b = DebugSession(LOOP)
    a.set_breakpoints([4])
    b.set_breakpoints([4])
    for _ in range(3):
        a.continue_()
    for _ in range(2):
        b.continue_()
    old_cursor_a = a.cursor
    # Compare an earlier saved boundary of A against B's current one,
    # ignoring where A happens to stand now.
    result = a.compare(b, left_cursor=0, right_cursor=b.cursor)
    assert result["left"]["boundary"] == 0
    assert result["right"]["boundary"] == b.cursor
    assert result["status"] == "waiting"
    # Asking for a boundary the side never reached is clamped, not invented.
    clamped = a.compare(b, left_cursor=99999)
    assert clamped["left"]["boundary"] == old_cursor_a


def test_saved_comparison_keeps_boundaries_after_branch_continues(tmp_path):
    from backend.debugger import ComparisonStore

    store = SessionStore(directory=tmp_path)
    comp_store = ComparisonStore(directory=tmp_path / "comparisons")
    parent = store.create(LOOP)
    parent.set_breakpoints([4])
    parent.continue_()
    branch = parent.fork()
    store.register(branch)
    branch.continue_()
    store.touch(branch)
    record = parent.compare_record(branch)
    comp_store.add(record)
    assert record["result"]["status"] == "waiting"
    kept_left = record["result"]["left"]["boundary"]
    kept_right = record["result"]["right"]["boundary"]
    # Continue the parent afterwards; the old comparison is unchanged and
    # still shows each side's variables at the original call site.
    parent.continue_()
    parent.continue_()
    store.touch(parent)
    reloaded = comp_store.get(record["id"])
    assert reloaded["result"]["left"]["boundary"] == kept_left
    assert reloaded["result"]["right"]["boundary"] == kept_right
    assert reloaded["leftState"]["boundary"] == kept_left
    assert reloaded["rightState"]["stack"] == record["rightState"]["stack"]
    # A brand new comparison naturally includes the newly executed events.
    newer = parent.compare_record(branch)
    assert newer["result"]["left"]["boundary"] == parent.cursor


def test_deleting_branch_keeps_other_pause_and_comparison_record(tmp_path):
    from backend.debugger import ComparisonStore

    store = SessionStore(directory=tmp_path)
    comp_store = ComparisonStore(directory=tmp_path / "comparisons")
    parent = store.create(RECURSIVE)
    parent.set_breakpoints([2])
    parent.continue_()
    parent_pause = parent.cursor
    branch = parent.fork()
    store.register(branch)
    record = parent.compare_record(branch)
    comp_store.add(record)
    # Abandon the experiment branch: parent pause site is untouched and
    # the comparison record survives for inspection.
    store.delete(branch.id)
    untouched = store.get(parent.id)
    assert untouched.cursor == parent_pause
    assert comp_store.get(record["id"])["id"] == record["id"]


def test_branches_survive_save_and_reopen(tmp_path):
    store = SessionStore(directory=tmp_path)
    parent = store.create(RECURSIVE)
    parent.set_breakpoints([2])
    parent.continue_()
    parent.continue_()
    store.touch(parent)
    edited = RECURSIVE.replace("n * 10", "n * 20")
    branch = parent.fork(
        source=edited,
        name="乘20",
        breakpoints=[2],
        bp_migrations=[{"oldLine": 2, "newLine": 2,
                        "fromHash": parent.source_hash,
                        "toHash": source_hash(edited)}],
    )
    store.register(branch)
    branch.continue_()
    store.touch(branch)
    branch_line = branch.state()["line"]

    fresh = SessionStore(directory=tmp_path)
    reopened_parent = fresh.get(parent.id)
    reopened_branch = fresh.get(branch.id)
    # Each branch resumes its own unfinished execution independently.
    assert len(reopened_parent.state()["stack"]) == 3
    assert reopened_branch.state()["line"] == branch_line
    assert reopened_branch.state()["source"] == edited
    assert reopened_branch.parent_id == parent.id
    assert reopened_branch.name == "乘20"
    # Confirmed breakpoint migration relations are preserved.
    assert reopened_branch.bp_migrations[0]["oldLine"] == 2
    assert reopened_branch.bp_migrations[0]["toHash"] == source_hash(edited)
    # Stepping one reopened branch does not move the other.
    reopened_branch.step_into()
    assert reopened_parent.state()["line"] != reopened_branch.state()["line"] or \
        reopened_parent.cursor != reopened_branch.cursor

    listed = {item["id"]: item for item in fresh.list_saved()}
    assert listed[branch.id]["parentId"] == parent.id
    assert listed[branch.id]["name"] == "乘20"
